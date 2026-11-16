#!/usr/bin/env python3
import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

from mutagen.mp4 import MP4


def getM4aPaths(rootDir: str) -> List[str]:
    m4aPaths: List[str] = []
    for dirPath, _, fileNames in os.walk(rootDir):
        for fileName in fileNames:
            lowerName = fileName.lower()
            if lowerName.endswith(".m4a") or lowerName.endswith(".mp4"):
                m4aPaths.append(os.path.join(dirPath, fileName))
    return sorted(m4aPaths)


def decodeTagValue(tagValue) -> str:
    if tagValue is None:
        return ""
    if isinstance(tagValue, list) and len(tagValue) == 0:
        return ""
    if isinstance(tagValue, list):
        tagValue = tagValue[0]
    if isinstance(tagValue, bytes):
        try:
            return tagValue.decode("utf-8", errors="replace")
        except Exception:
            return tagValue.decode("latin-1", errors="replace")
    return str(tagValue)


def isLyricLikeKey(key: str) -> bool:
    keyStr = str(key)
    keyLower = keyStr.lower()
    if keyLower in ["\xa9lyr", "©lyr"]:
        return True
    if keyLower.startswith("----:com.apple.itunes:"):
        return "lyric" in keyLower or "lyrics" in keyLower
    return "lyric" in keyLower or "lyrics" in keyLower


def extractLyricCandidates(tags: Dict) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    for key in tags.keys():
        if isLyricLikeKey(str(key)):
            textValue = decodeTagValue(tags.get(key))
            if textValue.strip():
                candidates.append((str(key), textValue))

    def sortKey(item: Tuple[str, str]) -> int:
        key = item[0]
        if key in ["\xa9lyr", "©lyr"]:
            return 0
        if key.lower().startswith("----:com.apple.itunes:"):
            return 1
        return 2

    return sorted(candidates, key=sortKey)


def readMp4Tags(filePath: str) -> Optional[Dict]:
    try:
        audio = MP4(filePath)
        return audio.tags or {}
    except Exception:
        return None


def printTagSummary(filePath: str, maxValueChars: int = 120) -> None:
    tags = readMp4Tags(filePath)
    if tags is None:
        print(f"[unreadable] {filePath}")
        return
    keys = sorted([str(k) for k in tags.keys()])
    print(f"\nFile: {filePath}")
    print(f"Keys ({len(keys)}):")
    for k in keys:
        if isLyricLikeKey(k):
            v = decodeTagValue(tags.get(k))
            vShort = (v[:maxValueChars] + "…") if len(v) > maxValueChars else v
            print(f"  * {k} = {repr(vShort)}")
        else:
            print(f"  - {k}")


def diffKeys(fileA: str, fileB: str) -> None:
    tagsA = readMp4Tags(fileA) or {}
    tagsB = readMp4Tags(fileB) or {}
    keysA = set([str(k) for k in tagsA.keys()])
    keysB = set([str(k) for k in tagsB.keys()])

    onlyA = sorted(keysA - keysB)
    onlyB = sorted(keysB - keysA)
    both = sorted(keysA & keysB)

    print("\n=== Key Diff ===")
    print(f"Only in A: {len(onlyA)}")
    for k in onlyA:
        print(f"  + {k}")
    print(f"\nOnly in B: {len(onlyB)}")
    for k in onlyB:
        print(f"  - {k}")

    lyricKeys = (
        [k for k in both if isLyricLikeKey(k)]
        + [k for k in onlyA if isLyricLikeKey(k)]
        + [k for k in onlyB if isLyricLikeKey(k)]
    )
    lyricKeys = sorted(set(lyricKeys))

    if lyricKeys:
        print("\nLyric-like keys seen in A/B:")
        for k in lyricKeys:
            aVal = decodeTagValue(tagsA.get(k))
            bVal = decodeTagValue(tagsB.get(k))
            aFlag = "A" if aVal.strip() else ""
            bFlag = "B" if bVal.strip() else ""
            print(f"  {k}: {aFlag}{'/' if aFlag and bFlag else ''}{bFlag}")


def normalizeLyricsText(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def fixFolder(folderPath: str, dryRun: bool) -> None:
    m4aPaths = getM4aPaths(folderPath)
    if not m4aPaths:
        print(f"No .m4a/.mp4 files found under: {folderPath}")
        return

    changedCount = 0
    skippedCount = 0
    noLyricsCount = 0
    errorCount = 0

    for filePath in m4aPaths:
        try:
            audio = MP4(filePath)
            tags = audio.tags or {}
            currentLyric = decodeTagValue(tags.get("\xa9lyr")).strip()
            if currentLyric:
                skippedCount += 1
                continue

            candidates = extractLyricCandidates(tags)
            candidates = [(k, v) for (k, v) in candidates if k not in ["\xa9lyr", "©lyr"]]
            if not candidates:
                noLyricsCount += 1
                continue

            sourceKey, sourceText = candidates[0]
            newText = normalizeLyricsText(sourceText)
            if not newText:
                noLyricsCount += 1
                continue

            print(f"[set ©lyr] {os.path.basename(filePath)}  (from {sourceKey})")
            if not dryRun:
                audio["\xa9lyr"] = [newText]
                audio.save()
            changedCount += 1

        except Exception as e:
            errorCount += 1
            print(f"[error] {filePath}: {e}")

    print("\n=== Fix Summary ===")
    print(f"Changed: {changedCount}")
    print(f"Skipped (already had ©lyr): {skippedCount}")
    print(f"No lyric candidates found: {noLyricsCount}")
    print(f"Errors: {errorCount}")
    if dryRun:
        print("\nDry-run only. Re-run with --apply to write changes.")


def countSuspiciousChars(text: str) -> Dict[str, int]:
    counts = {"nonAscii": 0, "nulls": 0, "cr": 0}
    for ch in text:
        code = ord(ch)
        if code == 0:
            counts["nulls"] += 1
        if ch == "\r":
            counts["cr"] += 1
        if code > 127:
            counts["nonAscii"] += 1
    return counts


def analyzeFile(filePath: str) -> None:
    tags = readMp4Tags(filePath)
    if tags is None:
        print(f"[unreadable] {filePath}")
        return

    lyricText = decodeTagValue(tags.get("\xa9lyr"))
    lyricText = lyricText if lyricText is not None else ""
    lyricText = str(lyricText)

    normalized = normalizeLyricsText(lyricText)
    counts = countSuspiciousChars(lyricText)

    print(f"\nFile: {filePath}")
    print(f"Has ©lyr: {'yes' if normalized.strip() else 'no'}")
    print(f"©lyr length: {len(lyricText)} (normalized {len(normalized)})")
    print(f"Suspicious chars: nonAscii={counts['nonAscii']} nulls={counts['nulls']} cr={counts['cr']}")

    freeformKeys = [
        str(k)
        for k in tags.keys()
        if str(k).lower().startswith("----:com.apple.itunes:")
    ]
    print(f"Freeform ---- keys: {len(freeformKeys)}")
    for k in sorted(freeformKeys)[:25]:
        print(f"  - {k}")
    if len(freeformKeys) > 25:
        print("  ...")


def cleanFreeformTagsForFile(filePath: str, dryRun: bool, keepKeys: List[str]) -> bool:
    audio = MP4(filePath)
    tags = audio.tags or {}
    keys = [str(k) for k in list(tags.keys())]

    removed: List[str] = []
    for key in keys:
        keyLower = key.lower()
        if keyLower.startswith("----:com.apple.itunes:"):
            if key in keepKeys:
                continue
            removed.append(key)
            try:
                del tags[key]
            except Exception:
                pass

    if removed:
        print(f"[clean] {os.path.basename(filePath)} removed {len(removed)} freeform keys")
        if not dryRun:
            audio.save()
        return True

    return False


def cleanFolder(folderPath: str, dryRun: bool, keepKeys: List[str]) -> None:
    m4aPaths = getM4aPaths(folderPath)
    if not m4aPaths:
        print(f"No .m4a/.mp4 files found under: {folderPath}")
        return

    changedCount = 0
    errorCount = 0

    for filePath in m4aPaths:
        try:
            changed = cleanFreeformTagsForFile(filePath, dryRun=dryRun, keepKeys=keepKeys)
            if changed:
                changedCount += 1
        except Exception as e:
            errorCount += 1
            print(f"[error] {filePath}: {e}")

    print("\n=== Clean Summary ===")
    print(f"Files changed: {changedCount}")
    print(f"Errors: {errorCount}")
    if dryRun:
        print("\nDry-run only. Re-run with --apply to write changes.")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    diffParser = subparsers.add_parser("diff", help="Diff MP4 tag keys between two files")
    diffParser.add_argument("--fileA", required=True)
    diffParser.add_argument("--fileB", required=True)

    showParser = subparsers.add_parser("show", help="Show tag keys for one file (highlights lyric-like fields)")
    showParser.add_argument("--file", required=True)

    analyzeParser = subparsers.add_parser("analyze", help="Analyze lyrics + freeform tags for one file")
    analyzeParser.add_argument("--file", required=True)

    fixParser = subparsers.add_parser("fix", help="Copy lyrics into ©lyr for all files in a folder (only when ©lyr is empty)")
    fixParser.add_argument("--folder", required=True)
    fixParser.add_argument("--apply", action="store_true", help="Actually write changes (default is dry-run)")

    cleanParser = subparsers.add_parser("clean", help="Remove ----:com.apple.iTunes:* tags from files in a folder")
    cleanParser.add_argument("--folder", required=True)
    cleanParser.add_argument("--apply", action="store_true", help="Actually write changes (default is dry-run)")
    cleanParser.add_argument("--keep", action="append", default=[], help="Exact freeform keys to keep (repeatable)")

    args = parser.parse_args()

    if args.command == "diff":
        diffKeys(args.fileA, args.fileB)
    elif args.command == "show":
        printTagSummary(args.file)
    elif args.command == "analyze":
        analyzeFile(args.file)
    elif args.command == "fix":
        fixFolder(args.folder, dryRun=(not args.apply))
    elif args.command == "clean":
        keepKeys = args.keep if isinstance(args.keep, list) else []
        cleanFolder(args.folder, dryRun=(not args.apply), keepKeys=keepKeys)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
