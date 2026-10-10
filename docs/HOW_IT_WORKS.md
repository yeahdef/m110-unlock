# How it works

A technical write-up of the M110 consumable lockout and the patch, so the change is
auditable.

## The hardware

The Phomemo M110 is built around a **JieLi AC635N** Bluetooth SoC (BR23 family). Its
CPU is JieLi's proprietary **pi32v2** core — not ARM, so stock disassemblers don't
apply. Code runs XIP (execute-in-place) from an external **1 MiB SPI NOR flash**,
decrypted on the fly by the chip's flash controller using a 16-bit **chip key** burned
into the SoC.

The roll "authentication" is an NFC/RFID tag on genuine rolls; the firmware reads it
and refuses to print when it's absent or invalid.

## The flash image

The flash uses JieLi's "new firmware" (JLFS) container:

- A flash header (locatable at a few fixed offsets) with a CRC.
- A table of file entries (`uboot.boot`, `isd_config.ini`, `app_dir_head`, …), each a
  32-byte record scrambled with the **ENC** stream cipher (key `0xFFFF`).
- `isd_config.ini` carries the chip key in an encoded 32-byte record — so the tool can
  recover the key from any dump without it being configured.
- The application lives under `app_dir_head` → `app_area_head`, with `app.bin` as the
  main code. This **app area is XIP-encrypted (the "SFC" cipher)**: each 32-byte block
  is ENC-ciphered with `chipkey ^ ((block_offset - app_base) >> 2)`. Both ciphers are
  XOR-based and self-inverse, so decrypt and encrypt are the same operation.

`patch_m110.py` recovers the key, decrypts the app area, edits `app.bin`, fixes the
affected CRCs (the `app.bin` entry and the enclosing `app_area_head` entry), and
re-encrypts only the changed 32-byte blocks.

## The lockout logic

In the decrypted `app.bin` (XIP base `0x1E00000`):

- A device-state struct lives in RAM at `0x1E290`. The byte at `+0x840` is the
  **consumable-valid flag** (`1` = a good roll is present). Related fields: `+0x20C`
  remaining-count, `+0x842` percent-remaining (drives the "running low" warning).
- The **validator** at `0x1E2E158` reads the RFID tag (over the reader interface),
  checks it, and returns `0` for valid / non-zero for invalid. Every place that sets
  `0x840 = 1` only does so after this returns `0`.
- The **print gate** is in the `GS v 0` raster-image command handler (`0x1E2FDC2`).
  Before printing it does, in effect:

  ```
  if (state[0x840] == 0)   -> reject (error 26)   // no valid roll
  if (state[0x83E] == 1)   -> reject               // secondary consumable check
  ... dimension checks ... -> print
  ```

## The patches

Two small edits, both located by byte-signature (so they track the code, not a fixed
address) and only applied where the surrounding bytes match exactly:

1. **Validator always returns "valid."** Replace the prologue of `0x1E2E158`
   (`77 04 e2 9c`) with `40 20 80 00` = `r0 = 0 ; rts`. Now the RFID check always
   reports a good roll, so `0x840` is set everywhere the firmware expects.

2. **Raster gate skips the consumable checks.** The gate's `jz` (`00 56`, "if invalid
   goto reject") becomes `04 8b` (`goto` straight to the dimension checks), bypassing
   the `0x840`/count/`0x83E` rejects in the print path while leaving the width/height
   validation intact.

Together they make printing proceed regardless of the roll, through both the state
flags and the print command itself.

## Why the chip key matters for sharing

The patched image is encrypted with **your** chip's key. A different unit may have a
different key; flashing a mismatched image would decrypt to garbage. That's why this
is a *tool that patches each person's own dump*, not a shareable binary — and why
`patch_m110.py` reads the key from the dump instead of assuming one.
