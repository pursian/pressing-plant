#!/usr/bin/env python3
"""
Pressing Plant (formerly runALAC): FLAC -> ALAC pipeline for the LOSSLESS HD library.

Lives in "LOSSLESS HD/Pressing Plant" together with its helpers:
  fixIpodLyrics.py  lyrics normalize + freeform tag clean
  libfix.py         library tag rules (original-year "[YYYY]" albums, main artist
                    only, other artists as "(feat. X)", artist spelling fixes)
  genreStep.py      one genre per artist: library artists get their library genre,
                    new artists are asked about in a window at the end
  overrides.json    manual fixes for libfix (spellings, album artists, soundtracks, years)
Python environments (beetsVenv, lyrFixVenv) stay in the home folder of whoever runs it.

usage:
  python3 pressingPlant.py                         # input ~/Downloads/MyFolder, output <input>/Lossless
  python3 pressingPlant.py --input ~/"Soulseek Downloads/complete"
  python3 pressingPlant.py --input <folder> --output <folder>
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass
class pipelineConfig:
    inputDir: Path
    outputDir: Path

    beetsVenvActivate: Path
    lyrFixVenvActivate: Path
    fixScriptPath: Path

    pipelineStateDir: Path
    beetsDbPath: Path
    beetsLogPath: Path
    convertLogPath: Path

    libfixPath: Path = Path()
    genreStepPath: Path = Path()
    flatDir: Path = Path()
    runLyricsFix: bool = True
    runFreeformClean: bool = True
    runLibraryTagRules: bool = True
    runGenreStep: bool = True


def runBash(command: str, logPath: Path | None = None) -> int:
    process = subprocess.Popen(
        ["bash", "-lc", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    outputLines: List[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        outputLines.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    exitCode = process.wait()

    if logPath is not None:
        logPath.parent.mkdir(parents=True, exist_ok=True)
        with logPath.open("a", encoding="utf-8") as f:
            f.write("".join(outputLines))

    return exitCode


def findFlacPaths(inputDir: Path) -> List[Path]:
    flacPaths: List[Path] = []
    for path in inputDir.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".flac":
            flacPaths.append(path)
    return sorted(flacPaths)


def probeTags(inputFile: Path) -> Dict[str, str]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(inputFile)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    tags = json.loads(result.stdout or "{}").get("format", {}).get("tags", {})
    return {str(k).lower(): str(v) for k, v in tags.items()}


def buildExtraMetadataArgs(tags: Dict[str, str]) -> List[str]:
    # ffmpeg drops FLAC tags it has no MP4 mapping for (TRACKTOTAL, DISCTOTAL,
    # "ALBUM ARTIST"), so fold them into fields it does map.
    extraArgs: List[str] = []

    for field, totalKeys in (("track", ("tracktotal", "totaltracks")), ("disc", ("disctotal", "totaldiscs"))):
        number = tags.get(field, "").strip()
        total = next((tags[k].strip() for k in totalKeys if tags.get(k, "").strip()), "")
        if number and total and "/" not in number:
            extraArgs += ["-metadata", f"{field}={number}/{total}"]

    if not tags.get("album_artist", "").strip():
        albumArtist = tags.get("album artist", "").strip()
        if albumArtist:
            extraArgs += ["-metadata", f"album_artist={albumArtist}"]

    return extraArgs


def buildFfmpegCommand(inputFile: Path, outputFile: Path, extraArgs: List[str]) -> str:
    inputQ = shlex.quote(str(inputFile))
    outputQ = shlex.quote(str(outputFile))
    extraQ = " ".join(shlex.quote(a) for a in extraArgs)
    return (
        f"ffmpeg -nostdin -y -i {inputQ} "
        f"-map 0:a -map '0:v?' "
        f"-c:a alac -c:v copy -disposition:v attached_pic "
        f"-map_metadata 0 -map_chapters 0 {extraQ} "
        f"-f mp4 {outputQ}"
    )


def convertFlacs(config: pipelineConfig) -> Tuple[int, int, int]:
    flacPaths = findFlacPaths(config.inputDir)
    if not flacPaths:
        print(f"No .flac files found under: {config.inputDir}")
        return (0, 0, 0)

    converted = 0
    skipped = 0
    errors = 0

    config.outputDir.mkdir(parents=True, exist_ok=True)
    delivered = loadManifest(config)

    for flacPath in flacPaths:
        relativePath = flacPath.relative_to(config.inputDir)
        outputPath = (config.outputDir / relativePath).with_suffix(".m4a")
        outputPath.parent.mkdir(parents=True, exist_ok=True)

        # Skipped even if you've since added it to Music and cleared the folder.
        # To convert something again, delete .beetsPipeline/converted.json.
        done = delivered.get(str(flacPath))
        if done:
            print(f"[skip already converted] {Path(done).name}")
            skipped += 1
            continue
        if outputPath.exists() and outputPath.stat().st_size > 0:
            print(f"[skip exists] {outputPath}")
            skipped += 1
            continue

        # Convert to a temp name and rename on success, so an interrupted or
        # failed conversion never leaves a truncated .m4a that later runs skip.
        partPath = outputPath.with_name(outputPath.name + ".part")

        print(f"[convert] {flacPath} -> {outputPath}")
        cmd = buildFfmpegCommand(flacPath, partPath, buildExtraMetadataArgs(probeTags(flacPath)))
        exitCode = runBash(cmd, logPath=config.convertLogPath)
        if exitCode == 0:
            partPath.replace(outputPath)
            converted += 1
        else:
            partPath.unlink(missing_ok=True)
            errors += 1
            print(f"[error] ffmpeg exit={exitCode} file={flacPath}")

    return (converted, skipped, errors)


def runBeetsOnLossless(config: pipelineConfig) -> None:
    losslessDirQ = shlex.quote(str(config.outputDir))
    beetsDbQ = shlex.quote(str(config.beetsDbPath))
    beetsLogQ = shlex.quote(str(config.beetsLogPath))

    # pipefail: without it, `beets ... | tee` reports tee's exit code and
    # beets failures go unnoticed.
    baseEnv = (
        f"set -o pipefail && "
        f"source {shlex.quote(str(config.beetsVenvActivate))} && "
        f"export PYTHONNOUSERSITE=1 && unset PYTHONPATH && "
    )

    # Fresh DB each run: stale rows would otherwise be re-written by `write -f`.
    config.beetsDbPath.unlink(missing_ok=True)

    # -s (singletons): an as-is album import overwrites albumartist on every
    # track in a group with the most common artist; singletons keep file tags.
    cmdImport = (
        baseEnv
        + f"python -m beets -l {beetsDbQ} import -s -A -C {losslessDirQ} "
        + f"2>&1 | tee -a {beetsLogQ}"
    )
    print("\n=== beets import (Lossless only) ===")
    exitCode = runBash(cmdImport)
    if exitCode != 0:
        raise RuntimeError(f"beets import failed exit={exitCode}")

    cmdLyrics = (
        baseEnv
        + f"python -m beets -l {beetsDbQ} -v lyrics path:{losslessDirQ} "
        + f"2>&1 | tee -a {beetsLogQ}"
    )
    print("\n=== beets lyrics (Lossless only) ===")
    exitCode = runBash(cmdLyrics)
    if exitCode != 0:
        raise RuntimeError(f"beets lyrics failed exit={exitCode}")

    cmdWrite = (
        baseEnv
        + f"python -m beets -l {beetsDbQ} write -f path:{losslessDirQ} "
        + f"2>&1 | tee -a {beetsLogQ}"
    )
    print("\n=== beets write -f (Lossless only) ===")
    exitCode = runBash(cmdWrite)
    if exitCode != 0:
        raise RuntimeError(f"beets write failed exit={exitCode}")



def runFixAndClean(config: pipelineConfig) -> None:
    losslessDirQ = shlex.quote(str(config.outputDir))

    baseEnv = (
        f"source {shlex.quote(str(config.lyrFixVenvActivate))} && "
        f"export PYTHONNOUSERSITE=1 && unset PYTHONPATH && "
    )

    if config.runLyricsFix:
        cmdFix = (
            baseEnv
            + f"python {shlex.quote(str(config.fixScriptPath))} fix "
            + f"--folder {losslessDirQ} --apply"
        )
        print("\n=== fixIpodLyrics.py fix (Lossless only) ===")
        exitCode = runBash(cmdFix)
        if exitCode != 0:
            raise RuntimeError(f"fix step failed exit={exitCode}")

    if config.runFreeformClean:
        cmdClean = (
            baseEnv
            + f"python {shlex.quote(str(config.fixScriptPath))} clean "
            + f"--folder {losslessDirQ} --apply"
        )
        print("\n=== fixIpodLyrics.py clean (Lossless only) ===")
        exitCode = runBash(cmdClean)
        if exitCode != 0:
            raise RuntimeError(f"clean step failed exit={exitCode}")


def runLibraryTagRules(config: pipelineConfig) -> None:
    """
    Apply the library's tag rules to the freshly converted files (they are not in
    Music yet, so writing the files directly is safe):
      - Year tag = album's ORIGINAL release year (MusicBrainz, then Apple's catalogue)
      - Album = "<title> [YYYY]"
      - Artist and Album Artist = the album's main artist only
      - every other credited artist -> title "(feat. A, B & C)"
      - songs with no cover get one: an image in the album's download folder
        (cover.jpg, folder.jpg, ...) or else the Cover Art Archive, shrunk to 600px
    Albums it can't settle on its own (several main artists, compilation-like,
    no year found) are left unchanged and listed at the end for review.
    """
    losslessDirQ = shlex.quote(str(config.outputDir))
    planQ = shlex.quote(str(config.pipelineStateDir / "libraryTagPlan.json"))
    cacheQ = shlex.quote(str(config.pipelineStateDir / "musicbrainzCache.json"))
    cmd = (
        f"set -o pipefail && "
        f"source {shlex.quote(str(config.lyrFixVenvActivate))} && "
        f"export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 LIBFIX_CACHE={cacheQ} && unset PYTHONPATH && "
        f"python {shlex.quote(str(config.libfixPath))} {losslessDirQ} {planQ} --all --write "
        f"--artwork {shlex.quote(str(config.inputDir))} "
        f"2>&1 | tee -a {shlex.quote(str(config.beetsLogPath))}"
    )
    print("\n=== Library tag rules: original year [YYYY], main artist, (feat. X), missing artwork ===")
    exitCode = runBash(cmd)
    if exitCode != 0:
        raise RuntimeError(f"library tag rules failed exit={exitCode}")


def runGenreStep(config: pipelineConfig) -> None:
    """
    One genre per artist. Songs whose album artist is already in the LOSSLESS HD
    library get that artist's library genre; for new artists a window asks for one
    (suggestions, your genres, or a new one) and lets you correct the artist name
    (others go into the titles as feat.). Only the new files are written.
    """
    cacheQ = shlex.quote(str(config.pipelineStateDir / "musicbrainzCache.json"))
    cmd = (
        f"source {shlex.quote(str(config.lyrFixVenvActivate))} && "
        f"export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 LIBFIX_CACHE={cacheQ} && unset PYTHONPATH && "
        f"python -u {shlex.quote(str(config.genreStepPath))} "
        f"{shlex.quote(str(config.outputDir))} {shlex.quote(str(config.pipelineStateDir))} "
        f"{shlex.quote(str(config.inputDir))}"
    )
    print("\n=== Genre: one genre per artist (new artists are asked at the end) ===")
    exitCode = runBash(cmd, logPath=config.beetsLogPath)
    if exitCode != 0:
        raise RuntimeError(f"genre step failed exit={exitCode}")


LOOKUP_SITES = ("musicbrainz.org", "itunes.apple.com", "lrclib.net", "coverartarchive.org")


def unreachableSites() -> List[str]:
    """Year, lyrics, cover and genre lookups all need these. curl ships with macOS."""
    down = list(LOOKUP_SITES)
    for attempt in range(3):  # a short Wi-Fi blip shouldn't stop the run
        if attempt:
            print(f"  can't reach {', '.join(down)}; trying again in 10 s ...")
            time.sleep(10)
        still = []
        for host in down:
            r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", "15", "-w", "%{http_code}", f"https://{host}/"],
                               capture_output=True, text=True)
            if r.returncode != 0 or not r.stdout.strip() or r.stdout.strip() == "000":
                still.append(host)
        down = still
        if not down:
            break
    return down


def manifestPath(config: pipelineConfig) -> Path:
    return config.pipelineStateDir / "converted.json"


def loadManifest(config: pipelineConfig) -> Dict[str, str]:
    p = manifestPath(config)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def flattenOutput(config: pipelineConfig) -> int:
    """
    Move every finished .m4a out of the hidden per-album work folders into ONE
    folder, so they can all be selected and added to Music at once. Music files
    them into Artist/Album folders itself. Name clashes get the album folder name
    added in front. Remembers which FLAC became which file, so re-runs skip them.
    """
    manifest = loadManifest(config)
    workDir, flatDir = config.outputDir, config.flatDir
    moved = 0
    for m4a in sorted(workDir.rglob("*.m4a")):
        rel = m4a.relative_to(workDir)
        target = flatDir / m4a.name
        if target.exists():
            prefix = " - ".join(rel.parent.parts) or "track"
            target = flatDir / f"{prefix} - {m4a.name}"
        n = 2
        while target.exists():
            target = flatDir / f"{m4a.stem} ({n}){m4a.suffix}"
            n += 1
        m4a.replace(target)
        manifest[str((config.inputDir / rel).with_suffix(".flac"))] = str(target)
        moved += 1
    for d in sorted((p for p in workDir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    manifestPath(config).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return moved


def openLosslessInFinder(config: pipelineConfig) -> None:
    cmdOpen = f"open {shlex.quote(str(config.flatDir))}"
    runBash(cmdOpen)


def main() -> None:
    homeDir = Path.home()
    scriptDir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Pressing Plant: FLAC -> ALAC pipeline")
    parser.add_argument("--input", help="folder to scan for .flac files (default ~/Downloads/MyFolder)")
    parser.add_argument("--output", help="where the .m4a files go (default <input>/Lossless)")
    args = parser.parse_args()
    inputDir = Path(args.input).expanduser().resolve() if args.input else homeDir / "Downloads" / "MyFolder"
    outputDir = Path(args.output).expanduser().resolve() if args.output else inputDir / "Lossless"

    # Songs end up directly in outputDir (one flat folder). While the pipeline runs
    # they sit in a hidden per-album work folder, because the year/feat step uses
    # folders to know which songs form an album.
    flatDir = outputDir
    outputDir = flatDir / ".work"
    pipelineStateDir = flatDir / ".beetsPipeline"
    beetsDbPath = pipelineStateDir / "losslessLibrary.db"
    beetsLogPath = pipelineStateDir / "beetsLossless.log"
    convertLogPath = pipelineStateDir / "ffmpegConvert.log"

    config = pipelineConfig(
        inputDir=inputDir,
        outputDir=outputDir,

        beetsVenvActivate=homeDir / "beetsVenv" / "bin" / "activate",
        lyrFixVenvActivate=homeDir / "lyrFixVenv" / "bin" / "activate",
        fixScriptPath=scriptDir / "fixIpodLyrics.py",
        libfixPath=scriptDir / "libfix.py",
        genreStepPath=scriptDir / "genreStep.py",
        flatDir=flatDir,

        pipelineStateDir=pipelineStateDir,
        beetsDbPath=beetsDbPath,
        beetsLogPath=beetsLogPath,
        convertLogPath=convertLogPath,

        runLyricsFix=True,
        runFreeformClean=True,
        runLibraryTagRules=True,
        runGenreStep=True,
    )

    if not config.inputDir.exists():
        raise SystemExit(f"Input dir not found: {config.inputDir}")
    if not config.beetsVenvActivate.exists():
        raise SystemExit(f"beets venv activate not found: {config.beetsVenvActivate}")
    if not config.lyrFixVenvActivate.exists():
        raise SystemExit(f"lyrFix venv activate not found: {config.lyrFixVenvActivate}")
    if not config.fixScriptPath.exists():
        raise SystemExit(f"fix script not found: {config.fixScriptPath}")
    if config.runLibraryTagRules and not config.libfixPath.exists():
        raise SystemExit(f"libfix script not found: {config.libfixPath}")
    if config.runGenreStep and not config.genreStepPath.exists():
        raise SystemExit(f"genre script not found: {config.genreStepPath}")

    config.pipelineStateDir.mkdir(parents=True, exist_ok=True)

    # Without internet every album comes out with no year, lyrics or cover and is
    # left unchanged, so stop before converting anything.
    print("=== Checking internet (MusicBrainz, Apple, LRCLIB, Cover Art Archive) ===")
    down = unreachableSites()
    if down:
        raise SystemExit(f"Can't reach {', '.join(down)}. Check the internet connection and run again. "
                         "Nothing was converted.")
    print("OK")

    print("\n=== Convert FLAC -> ALAC (.m4a) into Lossless ===")
    converted, skipped, errors = convertFlacs(config)

    print("\n=== Conversion summary ===")
    print(f"Converted: {converted}")
    print(f"Skipped:   {skipped}")
    print(f"Errors:    {errors}")

    # Nothing new (everything was converted on an earlier run): stop here instead of
    # running beets on an empty folder, which fails with "No matching items found".
    if not any(config.outputDir.rglob("*.m4a")):
        print("\nNothing new to convert. Done.")
        return

    print("\n=== beets: import + lyrics + force-write (Lossless only) ===")
    runBeetsOnLossless(config)

    print("\n=== Normalize lyrics tags + clean freeform tags (Lossless only) ===")
    runFixAndClean(config)

    # Last tag step, so nothing after it (beets write, clean) can undo it.
    if config.runLibraryTagRules:
        runLibraryTagRules(config)

    # After the tag rules, so the main artist is final. Asks about new artists last.
    if config.runGenreStep:
        runGenreStep(config)

    print("\n=== Move all songs into one folder ===")
    print(f"Songs ready in {config.flatDir}: {flattenOutput(config)}")

    print("\n=== Open Lossless in Finder ===")
    openLosslessInFinder(config)

    print("\nDone.")


if __name__ == "__main__":
    main()
