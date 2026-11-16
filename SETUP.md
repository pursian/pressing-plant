# Setting up Pressing Plant on a new Mac

One-time setup for each Mac account that runs Pressing Plant. It takes about 10 minutes. Checked against the working setup on 2026-11-16.

## What you need
| Thing | Version that works | Used for |
|---|---|---|
| macOS | any recent | `sips` (cover resizing) and `osascript` (genre window, reading Music) are built in |
| Python 3 | 3.10 or newer (3.11.2 from python.org works) | everything |
| ffmpeg + ffprobe | any recent | FLAC → ALAC conversion |
| beets | 2.5.1 | lyrics lookup |
| mutagen | 1.47.0 | reading and writing tags |

## 1. Python
If `python3 --version` shows nothing, or something older than 3.10, install Python from [python.org](https://www.python.org/downloads/macos/).
Then, in Finder, open **Applications → Python 3.x** and double-click **Install Certificates.command**. Without it, every online lookup fails and no album gets a year.

## 2. ffmpeg
Install [Homebrew](https://brew.sh) if you don't have it, then run:
```bash
brew install ffmpeg
```
If Homebrew belongs to a different account on this Mac, install ffmpeg from that account, or download a static build and put `ffmpeg` and `ffprobe` in `~/bin`.

## 3. Python environments
The scripts expect these exact folder names in your home folder.
```bash
python3 -m venv ~/beetsVenv && ~/beetsVenv/bin/python -m pip install "beets==2.5.1" beautifulsoup4 langdetect requests certifi "mutagen==1.47.0"
```
```bash
python3 -m venv ~/lyrFixVenv && ~/lyrFixVenv/bin/python -m pip install "mutagen==1.47.0" certifi
```

## 4. beets config
Create `~/.config/beets/config.yaml` with:
```yaml
library: "~/.config/beets/library.db"
plugins: [lyrics]
import:
  move: no
  copy: no
  write: yes
lyrics:
  auto: no
  synced: no
  force: no
  sources: [lrclib]
```

## 5. Get the files
Either copy this whole folder, or clone it:
```bash
git clone https://github.com/pursian/pressing-plant.git
```
Keep all the files together in one folder. The scripts find each other there.
- Create `overrides.json` for your own fixes: copy `overrides.example.json` to `overrides.json` and edit it, or start with just `{}`. It stays out of git. (The LOSSLESS HD copy lives on the drive.)

## 6. Check it works
- **Genre window:** this changes nothing.
  ```bash
  ~/lyrFixVenv/bin/python genreStep.py --preview
  ```
  Run it from this folder. A "New artist" window should open and respond to clicks.
- **Test conversion:** put one FLAC album folder in `~/Downloads/MyFolder`, then double-click `Pressing Plant.command`. The finished songs appear in `~/Downloads/MyFolder/Lossless`. If `~/Soulseek Downloads/complete` exists, the launcher reads from there instead and writes to `~/Downloads/Lossless`.
- Allow Terminal to control Music when macOS asks. The genre step uses that to read your genres, and only reads.

## Notes for the LOSSLESS HD setup
- Run it in the **iPod** account, with Music open on the LOSSLESS HD library.
- The FAT32 drive can't hold Python environments, so in the iPod account they live on the `LOSSLESS (APFS)` volume, with shortcuts in the home folder. That volume has to be mounted.
- Never point the output at the library's own music folder.
