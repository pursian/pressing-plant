# Pressing Plant

*(formerly runALAC; renamed 2026-11-16.)* Raw FLACs go in; finished, labelled albums come out.

Turns downloaded FLAC albums into ALAC (.m4a) files that already follow the LOSSLESS HD library's rules, ready to add to Music.
This folder holds the only live copy. Last updated 2026-11-16.

## How to run it
1. Use the **iPod** Mac account. Have Music open with the LOSSLESS HD library, so the genre step can read your genres.
2. Double-click **`Pressing Plant.command`**.
   - Input: `~/Soulseek Downloads/complete` if it exists, otherwise `~/Downloads/MyFolder`.
   - Output: `~/Downloads/Lossless`, all songs in one folder. Finder opens it at the end.
3. It first checks that MusicBrainz, Apple, LRCLIB and the Cover Art Archive can be reached. If not, it stops before converting anything.
4. When the run finishes, a window opens for each **new artist** (one not already in your library). Pick a genre there.
5. Select everything in the output folder and drag it into Music.

Never point the output at the library folder (`Media.localized/Music`). Music files the songs itself.

## What it does, in order
| Step | Script | What happens |
|---|---|---|
| 1. Convert | `pressingPlant.py` (ffmpeg) | FLAC → ALAC, lossless. Keeps tags and embedded cover. Already-converted FLACs are skipped (`.beetsPipeline/converted.json`). |
| 2. Lyrics | beets `lyrics` (LRCLIB) | Adds lyrics where they can be found. |
| 3. Lyrics cleanup | `fixIpodLyrics.py` | Makes lyrics show on the iPod and removes junk tags. |
| 4. Tag rules | `libfix.py` + `overrides.json` | Album = `Title [YYYY]` with the **original** release year (MusicBrainz, then Apple). Artist and Album Artist = main artist only. Other artists go into the title as `(feat. A, B & C)`. Artist spellings follow `overrides.json`. Adds a cover only to songs that have none. Albums it can't settle are left unchanged and listed at the end. |
| 5. Genre | `genreStep.py` (new 2026-11-16) | One genre per artist (see below). |
| 6. Collect | `pressingPlant.py` | Moves every song into one folder. |

## Genre rule
- Every artist has **one** genre.
- **Artist already in the library:** the new songs get the genre that artist already has. If the library has the artist under more than one genre, the most common one is used, and the run prints the others.
- **New artist:** at the very end, a native macOS window shows:
  - **Artist name**, editable. If it's a combined credit like `Calvin Harris & Ellie Goulding`, cut it down to the main artist. The album's tag rules then run again with that artist: everyone you removed goes into each song's title as `(feat. Ellie Goulding)`, and the year and cover are looked up again. If the name you type is already in your library, that artist's library genre is used (one genre per artist).
  - 1–3 genre suggestions from Apple Music and MusicBrainz, as buttons. Clicking one saves it.
  - **One of my genres** (a list) and **Or a new genre** (an empty box). After using either, press **Save with the genre from the list or box**.
  - **Skip (change nothing)** leaves the album as downloaded.
- Edition text is also handled for lookups: `Rumours (2004 Remaster)` is looked up on MusicBrainz as `Rumours`, so the year and cover are found. The album name itself keeps the edition text.
- Combined credits are also handled on their own: if MusicBrainz doesn't know `A & B` as one artist, the tag step tries `A`. Real duos and bands (Simon & Garfunkel) are found under their full name, so they're kept.
- Answers are remembered in `~/Downloads/Lossless/.beetsPipeline/` (`newArtistGenres.json` for genres, `artistRenames.json` for name edits), so you're asked once per artist, and the same combined credit is fixed automatically next time.
- Genres come from Music only if it's **already open** with the LOSSLESS HD library. The script never opens Music and refuses any other library. Otherwise it uses the list saved by the last run (`libraryGenres.json`).
- Only the new files are changed. Genres of songs already in Music are never touched.
- To see the window without converting anything:
  `~/lyrFixVenv/bin/python "/Volumes/LOSSLESS HD/Pressing Plant/genreStep.py" --preview`

## Files
| File | What it is |
|---|---|
| `Pressing Plant.command` | Double-click launcher. Picks input/output and runs `pressingPlant.py` from this folder. |
| `pressingPlant.py` | The pipeline (steps above). |
| `libfix.py` | Year / main artist / feat. / missing-cover rules. The SSL fix from 2026-11-12 is in it. |
| `fixIpodLyrics.py` | Lyrics normalizing and tag cleanup. |
| `genreStep.py` | Genre step and new-artist window. |
| `SETUP.md` | One-time setup for a new Mac or account. |
| `overrides.json` | Your manual fixes: artist spellings, album artists, soundtrack folders, years, allowed collaborations. **Not in git** (it's personal); `overrides.example.json` shows the format. |
| `Script Archive/` | Every older version from before git, named `YYYY-MM-DD HHMM name (note)` by the file's last-changed date. Not used by anything, and not in git. From now on git keeps the history. |

## What it needs (per Mac account)
- `~/beetsVenv` (beets 2.5.1) and `~/lyrFixVenv` (mutagen). In the iPod account both are shortcuts to `/Volumes/LOSSLESS (APFS)/iPod account/`, so that volume must be mounted.
- ffmpeg, and a beets config at `~/.config/beets/config.yaml`.
- Setup steps for a new Mac: **`SETUP.md`** in this folder.

## Version history
- **2026-11-16 (later):** renamed **Pressing Plant** (`runALAC.py` → `pressingPlant.py`, `runALAC.command` → `Pressing Plant.command`). The new-artist window was rebuilt as a native macOS window, because the Tk window ignored clicks. Put in git, with a private GitHub repo.
- **2026-11-16:** moved from the top level of LOSSLESS HD into this folder. Added `genreStep.py`. Tested end to end on 2 sample albums: year, main artist, lyrics, cover and genre all correct.
- **2026-11-13:** `overrides.json` additions.
- **2026-11-12:** `libfix.py` SSL fix. Before it, every MusicBrainz/Apple lookup failed and every album came out "no release year found online". This is what made the Nov 12 run look broken.
- **2026-11-06:** tag-rules step added (`libfix.py --write --artwork`), flat output folder, Soulseek input.
- **Not used:** `Script Archive/... libfix.py (v5, untested fork)`. It's a newer-featured libfix (keeps edition text, uses "(with X)" wording) that was never run on the library.

## Git and GitHub
- This folder is a git repository: GitHub repo `pursian/pressing-plant` (public). `Script Archive/`, `overrides.json` and macOS clutter files are kept out of it.
- After changing a script: commit with a short note of what changed, then push. Older versions: `git log`, or GitHub's history page.
- The drive is FAT32, so the repo is set to ignore file permissions (`core.fileMode false`).
