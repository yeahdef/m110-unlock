# m110-unlock

Make a **Phomemo M110** label printer print on **any** label roll — not just the
"genuine" rolls with a special RFID chip in them.

## Why this exists

Phomemo sells what looks like the same printer, the **M110**, in two flavors. Depending
on where in the world you buy it, you might get:

- a version that **happily prints on any label roll**, or
- a version that has a **lock**: a tiny RFID chip is embedded in Phomemo's own rolls,
  and the printer **refuses to print** unless it detects one.

Same printer name, same hardware — but if you happened to buy the locked version, you're
forced to keep buying Phomemo's specific rolls, while someone in another country got the
unlocked one for the same money. That's the itch this scratches: it turns a locked M110
into the unlocked M110 that other customers already have, so you can use cheaper or
third‑party rolls on the printer **you own**.

It does this by carefully editing the printer's own internal software (its "firmware")
to remove just the roll check — nothing else changes.

## Is this legal / safe to share?

This is a **right‑to‑repair** project. You're modifying a device you own. Importantly,
this project **does not contain any of Phomemo's software**. Instead:

- You copy your **own** printer's firmware off of it.
- This tool edits **that copy** (which is scrambled with a code unique to your printer).
- You put the edited copy back on.

So nothing copyrighted is handed around, and the edited file only works on the exact
printer it came from. What's shared here is the **method and the tool**, not a cracked
copy of anyone's software.

## ⚠️ Please read before you start

- **You're changing firmware. There's always some risk of bricking a device.** Go slow.
- **Save your original firmware copy and don't lose it.** It's your undo button — if
  anything goes wrong, you put it back and you're exactly where you started.
- This has been **tested on one printer, one firmware version** (the M110 using the
  "AC635N" chip). The tool checks that your printer matches before changing anything and
  stops safely if it doesn't — but it hasn't been tested on every version out there.
- Only do this to a printer you own.

If that all sounds fine, here's the whole thing.

## What you need

**Hardware**
- A **JieLi USB download dongle** — a small, cheap USB gadget (sold as a "JL USB
  Updater" / forced‑download tool). It's what lets your computer talk to the printer's
  chip in recovery mode.
- A **USB‑C data cable** for the printer. ⚠️ Lots of USB cables only carry power, not
  data — test yours by plugging a phone in with it first; if the phone shows up on your
  computer, the cable is fine.
- A computer. The steps below are for Linux; the flashing tool also runs on Windows/Mac.

**Software** (free; install from the links — this project doesn't bundle them)
- [`jl-uboot-tool`](https://github.com/kagaimiq/jl-uboot-tool) — reads the firmware off
  the printer and writes it back.
- **Python 3** (already on most computers) — to run `patch_m110.py` from this project.

## Step by step

### 1. Put the printer into recovery mode
Connect: **computer → dongle → printer** (with your data cable).
1. Turn the printer **fully off**.
2. **Press and hold** the dongle's download button, you might have to click several times, when it is red - continue holding (when the light is RED, you're good).
3. **While still holding it, turn the printer on.** Its screen stays **blank** — that's
   normal and means it worked. Your computer now sees the printer's chip as a little disk.

*(On Linux it shows up as `/dev/sg0`. If it's missing, run `sudo modprobe sg` once.)*

### 2. Copy your firmware off — and back it up
Using `jl-uboot-tool`, save a copy of the printer's firmware:
```bash
sudo ./jluboottool.py --device /dev/sg0 "read 0 0x100000 m110_backup.bin"
```
**Now copy `m110_backup.bin` somewhere safe.** This is your undo button. Don't skip this.

### 3. Make the unlocked version
```bash
python3 patch_m110.py m110_backup.bin -o m110_patched.bin
```
The tool reads your printer's unique code out of the backup, unscrambles the firmware,
removes just the roll check, puts it back together correctly, and double‑checks its own
work before saving `m110_patched.bin`.

### 4. Put the unlocked version on the printer
Still in recovery mode:
```bash
sudo ./jluboottool.py --device /dev/sg0 "write 0 m110_patched.bin"
```
Optional, recommended — read it back to confirm it wrote correctly:
```bash
sudo ./jluboottool.py --device /dev/sg0 "read 0 0x100000 readback.bin"
cmp readback.bin m110_patched.bin && echo "flash verified"
```

### 5. Turn it on and try it
unplug the printer from USB dongle, Power the printer off, then back on (you'll see the usual startup screen). Load any
roll — Phomemo's, a third‑party one, whatever — and print. Done.

### If something goes wrong
Put your backup back and you're exactly where you started:
```bash
sudo ./jluboottool.py --device /dev/sg0 "write 0 m110_backup.bin"
```

## Want the technical details?

[`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) explains exactly what the printer's
firmware looks like, where the roll check lives, and the two tiny edits that remove it —
so the change is auditable, not a black box.

## Credits

The understanding of JieLi's firmware format builds on the public reverse‑engineering
work by **kagaimiq** ([jielie](https://github.com/kagaimiq/jielie),
[jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool),
[jl-misctools](https://github.com/kagaimiq/jl-misctools)). This project is an independent
re‑implementation and includes none of Phomemo's software.

## License

MIT — see [LICENSE](LICENSE).
