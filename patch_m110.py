#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
patch_m110.py - remove the RFID/consumable lockout from a Phomemo M110 firmware dump.

Right-to-repair tool. It does NOT contain or redistribute any Phomemo firmware.
You dump YOUR OWN printer's flash, this patches that dump (keyed to your own chip),
and you flash it back. See README.md for the full procedure and hardware needed.

Usage:
    python3 patch_m110.py <your_dump.bin> [-o <output.bin>]

It will:
  * auto-detect your chip's key from the dump,
  * decrypt the firmware,
  * locate the consumable gate by byte-signature (not a fixed address),
  * apply two small patches so printing no longer requires a genuine roll,
  * fix the firmware CRCs, re-encrypt with YOUR key, and
  * verify the result round-trips cleanly before writing it.

Firmware-format understanding is based on the public JieLi reverse-engineering
notes by kagaimiq (github.com/kagaimiq/jielie, jl-misctools). This is an
independent re-implementation; no vendor code is included.
"""

import sys
import struct
import argparse

CHIP_STR = b"AC635N"            # expected chip id in the flash header (BR23 family / M110)
HEADER_OFFSETS = [0, 0x1000, 0x10000, 0x80000, 0x100000, 0x180000]

# ---------------------------------------------------------------------------
# Ciphers and CRC (JieLi flash format).  All are XOR-based and self-inverse,
# so "decrypt" and "encrypt" are the same operation.
# ---------------------------------------------------------------------------

def jl_enc(buf, key=0xFFFF):
    """JieLi 'ENC' stream cipher over a bytes-like; returns a new bytearray."""
    out = bytearray(len(buf))
    k = key
    for i, b in enumerate(buf):
        out[i] = b ^ (k & 0xFF)
        k = ((k << 1) ^ (0x1021 if k & 0x8000 else 0)) & 0xFFFF
    return out

def sfc_key(block_off, base, chipkey):
    """Per-block key for the XIP (SFC) cipher of the app area."""
    return chipkey ^ (((block_off - base) >> 2) & 0xFFFF)

def sfc_xcrypt_inplace(buf, base, chipkey, start, end):
    """(De)crypt the SFC app area in buf[start:end] in place, 32 bytes at a time."""
    start &= ~31
    for o in range(start, end, 32):
        buf[o:o + 32] = jl_enc(buf[o:o + 32], sfc_key(o, base, chipkey))

def jl_crc16(data, crc=0):
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

def chipkey_decode(blob):
    """Decode the 32-byte chipkey record found in isd_config.ini."""
    if len(blob) < 32:
        raise ValueError("chipkey blob too short")
    s = sum(blob[:16]) & 0xFF
    if s >= 0xE0:
        s = 0xAA
    elif s <= 0x10:
        s = 0x55
    key = 0
    for i in range(16):
        if (blob[16 + i] ^ blob[15 - i]) < s:
            key |= (1 << i)
    return key

# ---------------------------------------------------------------------------
# Firmware structure parsing (JieLi "new-fw" / JLFS)
# ---------------------------------------------------------------------------

class Entry:
    __slots__ = ("hdr_off", "data_crc", "eoff", "esize", "flags", "index", "name",
                 "data_off", "data_size")

    def __init__(self, buf, hdr_off, database, data_after_header):
        hcrc, hdr = struct.unpack_from("<H30s", buf, hdr_off)
        if jl_crc16(hdr) != hcrc:
            raise ValueError("JLFS entry header CRC mismatch @0x%X" % hdr_off)
        edcrc, eoff, esize, flags, _resv, index, name = struct.unpack("<HIIBBH16s", hdr)
        self.hdr_off = hdr_off
        self.data_crc = edcrc
        self.eoff = eoff
        self.esize = esize
        self.flags = flags
        self.index = index
        self.name = name.split(b"\0")[0].decode("ascii", "replace")
        if data_after_header:
            self.data_off = hdr_off + 32
            self.data_size = hdr_off + esize - self.data_off
        else:
            self.data_off = eoff + database
            self.data_size = esize


def find_base(dump):
    for off in HEADER_OFFSETS:
        if off + 32 > len(dump):
            continue
        h = jl_enc(dump[off:off + 32], 0xFFFF)
        hcrc = int.from_bytes(h[:2], "little")
        if hcrc != 0 and jl_crc16(h[2:]) == hcrc:
            # splice back the plaintext VID/PID the format keeps unscrambled
            chip = dump[off + 16:off + 32]
            if CHIP_STR in chip:
                return off, chip
            return off, chip
    raise RuntimeError("Not a recognizable M110/BR23 firmware (no valid flash header).")


def parse(dump):
    """Return (chipkey, base, work_buffer, app_area_head Entry, app.bin Entry)."""
    base, chip = find_base(dump)
    if CHIP_STR not in chip:
        print("  ! warning: chip id in header is %r, expected %r" % (chip.split(b'\xff')[0], CHIP_STR))

    # top-level entries are scrambled with key 0xFFFF
    work = bytearray(dump)
    off = base + 0x20
    tops = []
    while True:
        work[off:off + 32] = jl_enc(work[off:off + 32], 0xFFFF)
        e = Entry(work, off, base, data_after_header=False)
        tops.append(e)
        off += 32
        if e.index != 0:
            break

    appbase = None
    chipkey = None
    for e in tops:
        if e.name == "app_dir_head" and appbase is None:
            appbase = e.data_off
        elif e.name == "isd_config.ini":
            blob, crc = struct.unpack_from("<32sH", dump, e.data_off)   # stored unencrypted
            if jl_crc16(blob) == crc:
                chipkey = chipkey_decode(blob)
    if appbase is None:
        raise RuntimeError("app_dir_head not found.")
    if chipkey is None:
        raise RuntimeError("Could not recover chipkey from isd_config.ini.")

    # the app area is SFC-encrypted; decrypt its header block to learn its size
    sfc_xcrypt_inplace(work, appbase, chipkey, appbase, appbase + 32)
    aah = Entry(work, appbase, appbase, data_after_header=True)
    # decrypt the rest of the app area (start AFTER the header block already done
    # above, so it is not re-scrambled by this self-inverse pass)
    sfc_xcrypt_inplace(work, appbase, chipkey, appbase + 32, appbase + aah.esize)

    # app files are entries living inside the app_area_head data region
    appbin = None
    off = aah.data_off
    while off < aah.hdr_off + aah.esize:
        e = Entry(work, off, appbase, data_after_header=False)
        if e.name == "app.bin":
            appbin = e
        off += 32
        if e.index != 0:
            break
    if appbin is None:
        raise RuntimeError("app.bin entry not found in app area.")

    return chipkey, base, work, aah, appbin

# ---------------------------------------------------------------------------
# The patches.  Each is located by a byte-signature inside decrypted app.bin,
# so it tracks the code instead of a hardcoded address.  The signature only
# matches when the surrounding code is identical to the version this was built
# against, which is exactly when the fixed-up bytes are known to be correct.
# ---------------------------------------------------------------------------

PATCHES = [
    dict(
        name="rfid-validator-return-valid",
        # prologue of the consumable/RFID check function; replace it with "r0=0; rts"
        sig=bytes.fromhex("77 04 e2 9c 40 20 41 20 d0 e9 09 00".replace(" ", "")),
        at=0,
        old=bytes.fromhex("7704e29c"),
        new=bytes.fromhex("40208000"),  # r0 = 0 ; rts  -> always "valid"
    ),
    dict(
        name="raster-gate-skip",
        # consumable gate in the GS-v-0 raster handler: jz r0 -> reject
        sig=bytes.fromhex("d8 ee b0 00 cc ff b0 87 00 00 00 56".replace(" ", "")),
        at=10,
        old=bytes.fromhex("0056"),
        new=bytes.fromhex("048b"),       # goto past all consumable checks
    ),
]


def apply_patches(work, appbin):
    a0, a1 = appbin.data_off, appbin.data_off + appbin.data_size
    region = bytes(work[a0:a1])
    touched = []
    for p in PATCHES:
        sig = p["sig"]
        hits = []
        i = region.find(sig)
        while i != -1:
            hits.append(i)
            i = region.find(sig, i + 1)
        if len(hits) == 0:
            raise RuntimeError("patch %r: signature not found - your firmware version "
                               "differs; see docs/HOW_IT_WORKS.md to locate it manually." % p["name"])
        if len(hits) > 1:
            raise RuntimeError("patch %r: signature not unique (%d hits); aborting."
                               % (p["name"], len(hits)))
        pos = a0 + hits[0] + p["at"]
        if bytes(work[pos:pos + len(p["old"])]) != p["old"]:
            raise RuntimeError("patch %r: unexpected bytes at target; aborting." % p["name"])
        work[pos:pos + len(p["new"])] = p["new"]
        touched.append(pos)
        print("  patched %-28s @ app.bin+0x%X" % (p["name"], pos - a0))
    return touched


def fix_crcs(work, aah, appbin):
    # app.bin data crc + its entry header crc
    work[appbin.hdr_off + 2:appbin.hdr_off + 4] = \
        jl_crc16(work[appbin.data_off:appbin.data_off + appbin.data_size]).to_bytes(2, "little")
    work[appbin.hdr_off:appbin.hdr_off + 2] = \
        jl_crc16(work[appbin.hdr_off + 2:appbin.hdr_off + 32]).to_bytes(2, "little")
    # app_area_head data crc + header crc (covers everything above)
    work[aah.hdr_off + 2:aah.hdr_off + 4] = \
        jl_crc16(work[aah.data_off:aah.data_off + aah.data_size]).to_bytes(2, "little")
    work[aah.hdr_off:aah.hdr_off + 2] = \
        jl_crc16(work[aah.hdr_off + 2:aah.hdr_off + 32]).to_bytes(2, "little")
    return [appbin.hdr_off, aah.hdr_off]

# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Remove the Phomemo M110 RFID/consumable lockout "
                                             "from your own firmware dump.")
    ap.add_argument("dump", help="raw flash dump read from YOUR printer")
    ap.add_argument("-o", "--output", help="output file (default: <dump>_patched.bin)")
    args = ap.parse_args()

    out_path = args.output or (args.dump.rsplit(".", 1)[0] + "_patched.bin")
    if out_path == args.dump:
        sys.exit("refusing to overwrite the input dump; choose a different -o")

    dump = bytearray(open(args.dump, "rb").read())
    print("[*] parsing %s (%d bytes)" % (args.dump, len(dump)))
    chipkey, base, work, aah, appbin = parse(dump)
    print("    chipkey 0x%04X | app.bin @0x%X (%d bytes)" % (chipkey, appbin.data_off, appbin.data_size))

    print("[*] applying patches")
    touched = apply_patches(work, appbin)

    print("[*] fixing CRCs")
    touched += fix_crcs(work, aah, appbin)

    print("[*] re-encrypting with your chipkey")
    out = bytearray(dump)                       # original ciphertext
    for pos in touched:
        o = pos & ~31
        out[o:o + 32] = jl_enc(work[o:o + 32], sfc_key(o, aah.hdr_off, chipkey))

    print("[*] verifying round-trip")
    ck2, _, work2, aah2, appbin2 = parse(bytes(out))
    region2 = bytes(work2[appbin2.data_off:appbin2.data_off + appbin2.data_size])
    ok = True
    for p in PATCHES:
        # signature's 'old' replaced by 'new' => the sig with new bytes spliced must be present
        newsig = bytearray(p["sig"]); newsig[p["at"]:p["at"] + len(p["new"])] = p["new"]
        if bytes(newsig) not in region2:
            ok = False
            print("    ! verify failed for %s" % p["name"])
    if ck2 != chipkey or not ok:
        sys.exit("verification FAILED - not writing output.")
    print("    OK: re-decrypts cleanly, chipkey 0x%04X, patches present" % ck2)

    open(out_path, "wb").write(out)
    print("[+] wrote %s" % out_path)
    print("    flash it back with jl-uboot-tool (see README). KEEP YOUR ORIGINAL DUMP.")


if __name__ == "__main__":
    main()
