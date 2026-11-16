#!/usr/bin/env python3
"""
Plan tag fixes for album folders in the LOSSLESS HD library.

For every album folder given, it works out:
  * the album's ORIGINAL release year (MusicBrainz release group first-release-date)
  * album name = "<clean title> [YYYY]"
  * artist = album's main artist only (canonical spelling); album artist = same
  * every other credited artist (MusicBrainz recording credits + anything already
    in the tags) appended to the song title as "(feat. A, B & C)"
  * soundtrack folders: artist/album artist = the franchise name, real artist moved
    into the title as "Real Artist – Title (feat. X)"

Nothing is written to files here. Output is a JSON plan that applyPlan.js applies
through the Music app.

usage: libfix.py <musicRoot> <planOut.json> <albumFolder> [<albumFolder> ...]
       libfix.py <musicRoot> <planOut.json> --all [--write]
       (album folders are relative to musicRoot, e.g. "AC_DC/Back In Black";
        --all = every folder under musicRoot that holds songs;
        --write = write the new tags straight into the files with mutagen. Only use
        this for files that are NOT in the Music library yet (e.g. fresh conversions);
        library files must go through applyPlan.js so Music stays in sync.)
"""
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import ssl
import urllib.error
import urllib.parse
import urllib.request

from mutagen import File
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
CACHE_PATH = os.environ.get("LIBFIX_CACHE") or (
    os.path.join(PROJECT, "reports", "musicbrainz-cache.json")
    if os.path.isdir(os.path.join(PROJECT, "reports"))
    else os.path.join(os.path.expanduser("~"), ".libfix-musicbrainz-cache.json")
)
OVERRIDES_PATH = os.environ.get("LIBFIX_OVERRIDES") or os.path.join(HERE, "overrides.json")
USER_AGENT = "ALACLibraryTools/1.0 (personal library tagging script)"


def _sslContext():
    # python.org Python ships without trusted certificates, so every HTTPS lookup
    # (MusicBrainz, Apple, Cover Art Archive) failed with CERTIFICATE_VERIFY_FAILED
    # and every album ended up "no release year found online". Use certifi if
    # installed, else the macOS system bundle.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    if os.path.exists("/etc/ssl/cert.pem"):
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ssl.create_default_context()


SSL_CONTEXT = _sslContext()


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
        return r.read()

YEAR_SUFFIX = re.compile(r"\s*\[(\d{4})\]\s*$")
FEAT_SEG = re.compile(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]]+)[\)\]]", re.I)
FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+(.+)$", re.I)
# "featuring" must come before "feat", and "feat"/"ft" need a word boundary after
# them, or "Santana Featuring Eric Clapton" splits into "Santana", "uring Eric Clapton".
NAME_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bx\b|\bfeaturing\b|\bfeat\b\.?|\bft\b\.?|\bwith\b|/)\s*", re.I)
EDITION = re.compile(
    r"\s*[\(\[][^\)\]]*(deluxe|edition|remaster|expanded|explicit|clean|version|anniversary|bonus)[^\)\]]*[\)\]]",
    re.I,
)


# ---------------------------------------------------------------- helpers
def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    return " ".join(s.split()).casefold()


def key(s):
    s = norm(s).replace("&", "and")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s)


def cleanAlbumTitle(album):
    t = YEAR_SUFFIX.sub("", album or "").strip()
    t = re.sub(r"\s+-\s+(single|ep)$", "", t, flags=re.I)
    return t


def baseAlbumKey(album):
    return key(EDITION.sub("", cleanAlbumTitle(album)))


def joinNames(names):
    # MusicBrainz uses the Unicode hyphen (e.g. "K‐Ci & JoJo"); iPod fonts may not draw it.
    names = [n.replace("\u2010", "-").replace("\u2011", "-") for n in names]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " & " + names[-1]


def splitNames(text):
    return [n.strip() for n in NAME_SPLIT.split(text or "") if n.strip()]


def stripFeat(title):
    """Return (title without feat. segment, [names already credited in title])."""
    names = []
    m = FEAT_SEG.search(title)
    if m:
        names = splitNames(m.group(1))
        title = (title[: m.start()] + title[m.end():])
    else:
        m = FEAT_TAIL.search(title)
        if m:
            names = splitNames(m.group(1))
            title = title[: m.start()]
    return " ".join(title.split()), names


def addFeat(title, names):
    if not names:
        return title
    return f"{title} (feat. {joinNames(names)})"


# ---------------------------------------------------------------- tags
def readTags(path):
    if path.lower().endswith(".m4a"):
        t = MP4(path).tags or {}

        def g(k):
            v = t.get(k)
            if not v:
                return ""
            v = v[0]
            return str(v[0] if isinstance(v, tuple) else v)

        disk = t.get("disk")
        trkn = t.get("trkn")
        return {
            "title": g("\xa9nam"), "artist": g("\xa9ART"), "albumArtist": g("aART"),
            "album": g("\xa9alb"), "year": g("\xa9day")[:4],
            "disc": disk[0][0] if disk else 1, "track": trkn[0][0] if trkn else 0,
        }
    t = ID3(path)

    def g(k):
        v = t.get(k)
        return str(v.text[0]) if v and v.text else ""

    def num(k):
        try:
            return int(g(k).split("/")[0])
        except ValueError:
            return 0

    return {
        "title": g("TIT2"), "artist": g("TPE1"), "albumArtist": g("TPE2"),
        "album": g("TALB"), "year": (g("TDRC") or g("TYER"))[:4],
        "disc": num("TPOS") or 1, "track": num("TRCK"),
    }


# ---------------------------------------------------------------- MusicBrainz
class MusicBrainz:
    def __init__(self):
        self.cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
        self.last = 0.0

    def save(self):
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        json.dump(self.cache, open(tmp, "w"), ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)

    def get(self, path, params):
        url = "https://musicbrainz.org/ws/2/" + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"})
        if url in self.cache:
            return self.cache[url]
        for attempt in range(4):
            wait = 1.1 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                data = json.loads(fetch(url).decode("utf-8"))
                self.cache[url] = data
                return data
            except urllib.error.HTTPError as e:
                if e.code in (503, 429):
                    time.sleep(3 + attempt * 3)
                    continue
                if e.code == 404:
                    return None
                raise
            except Exception as e:
                print(f"  [lookup error] MusicBrainz: {e}", file=sys.stderr)
                time.sleep(3)
        return None

    @staticmethod
    def creditNames(credit):
        # The name as printed on the release (e.g. "Kanye West"), not the artist's
        # current MusicBrainz name (e.g. "Ye").
        return [c.get("name") or c["artist"]["name"] for c in (credit or [])]

    def findReleaseGroup(self, album, artist, trackCount=10, soundtrack=False):
        title = cleanAlbumTitle(album)
        q = f'releasegroup:"{title}" AND artist:"{artist}"'
        data = self.get("release-group", {"query": q, "limit": 25}) or {}
        want, wantBase, artKey = key(title), baseAlbumKey(title), key(artist)
        best = None
        for g in data.get("release-groups", []):
            names = [key(n) for n in self.creditNames(g.get("artist-credit"))]
            creditStr = key("".join(c.get("name", "") + c.get("joinphrase", "") for c in g.get("artist-credit", [])))
            if artKey not in names and artKey != creditStr:
                continue
            t, tb = key(g["title"]), baseAlbumKey(g["title"])
            if t == want:
                rank = 0
            elif tb == wantBase:
                rank = 1
            else:
                continue
            date = g.get("first-release-date") or ""
            order = ["Single", "EP", "Album"] if trackCount <= 3 else ["Album", "EP", "Single"]
            ptype = g.get("primary-type")
            typeRank = order.index(ptype) if ptype in order else 3
            # Several release groups can share a title (reissues, box sets, live or
            # compilation entries). Prefer plain studio entries, then the EARLIEST
            # date, because the original release is the one we want.
            secondary = [x.lower() for x in (g.get("secondary-types") or [])]
            wantSecondary = any(w in want for w in ("live", "greatest", "best", "hits", "remix", "collection", "essential", "gold"))
            secPenalty = 0 if (not secondary or wantSecondary) else 1
            cand = (rank, typeRank, secPenalty, date or "9999", -g.get("score", 0), g)
            if best is None or cand[:5] < best[:5]:
                best = cand
        if best is None and soundtrack:
            # Soundtracks are usually titled "<Film>: Original Motion Picture Soundtrack".
            q = f'releasegroup:"{title}" AND secondarytype:soundtrack'
            data = self.get("release-group", {"query": q, "limit": 25}) or {}
            for g in data.get("release-groups", []):
                if key(g["title"]).startswith(baseAlbumKey(title)) and "various" in key(
                    "".join(self.creditNames(g.get("artist-credit")))
                ):
                    return g
        return best[5] if best else None

    def bestRelease(self, rgId, trackCount):
        data = self.get("release", {"release-group": rgId, "inc": "media", "limit": 100}) or {}
        rels = data.get("releases", [])
        if not rels:
            return None

        def score(r):
            n = sum(m.get("track-count", 0) for m in r.get("media", []))
            return (
                abs(n - trackCount),
                0 if r.get("status") == "Official" else 1,
                0 if r.get("country") in ("US", "XW") else 1,
                r.get("date") or "9999",
            )

        rel = sorted(rels, key=score)[0]
        return self.get("release/" + rel["id"], {"inc": "recordings artist-credits"})


def itunesYear(mb, album, artist):
    """Second opinion when MusicBrainz has nothing: Apple's catalogue (iTunes Search API)."""
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": f"{artist} {cleanAlbumTitle(album)}", "entity": "album", "limit": 25})
    if url not in mb.cache:
        time.sleep(3)  # Apple allows roughly 20 requests a minute
        try:
            mb.cache[url] = json.loads(fetch(url).decode("utf-8"))
        except Exception as e:
            print(f"  [lookup error] Apple: {e}", file=sys.stderr)
            return None
    years = []
    for c in mb.cache[url].get("results", []):
        if baseAlbumKey(c.get("collectionName", "")) == baseAlbumKey(album) and key(artist) in key(c.get("artistName", "")):
            if c.get("releaseDate"):
                years.append(c["releaseDate"][:4])
    return min(years) if years else None


# ---------------------------------------------------------------- planning
def matchTracks(ours, mbTracks):
    """ours: list of tag dicts. mbTracks: list of (disc, pos, title, creditNames). Returns {idx: mbTrack}."""
    out = {}
    byPos = {(d, p): t for (d, p, *_rest) in mbTracks for t in [(d, p, *_rest)]}
    used = set()
    for i, o in enumerate(ours):
        base = key(stripFeat(o["title"])[0])
        cand = byPos.get((o["disc"], o["track"]))
        if cand and difflib.SequenceMatcher(None, base, key(cand[2])).ratio() >= 0.6:
            out[i] = cand
            used.add((cand[0], cand[1]))
    for i, o in enumerate(ours):
        if i in out:
            continue
        base = key(stripFeat(o["title"])[0])
        best, bestR = None, 0.0
        for t in mbTracks:
            if (t[0], t[1]) in used:
                continue
            r = difflib.SequenceMatcher(None, base, key(t[2])).ratio()
            if r > bestR:
                best, bestR = t, r
        if best and bestR >= 0.75:
            out[i] = best
            used.add((best[0], best[1]))
    return out


def planAlbum(root, folder, mb, ov):
    full = os.path.join(root, folder)
    files = sorted(
        os.path.join(full, f) for f in os.listdir(full)
        if not f.startswith("._") and f.lower().endswith((".m4a", ".mp3"))
    )
    ours = [dict(readTags(p), path=os.path.relpath(p, root)) for p in files]
    notes = []
    if not ours:
        return {"folder": folder, "tracks": [], "notes": ["no songs"]}

    canon = {norm(k): v for k, v in ov.get("artistSpelling", {}).items()}
    fix = lambda name: canon.get(norm(name), " ".join((name or "").split()))

    soundtrack = ov.get("soundtracks", {}).get(folder)
    albumOv = ov.get("albumArtist", {}).get(folder)
    tagAA = [o["albumArtist"] for o in ours if o["albumArtist"].strip()]
    tagA = [o["artist"] for o in ours if o["artist"].strip()]
    if albumOv:
        mainArtist = albumOv
    elif tagAA:
        mainArtist = max(set(tagAA), key=tagAA.count)
    else:
        # No album artist tag: use the most common song artist, or its first name
        # when every song's artist starts with the same person (e.g. "Drake & 21 Savage").
        mainArtist = max(set(tagA), key=tagA.count) if tagA else folder.split("/")[0]
        firsts = {key(splitNames(a)[0]) for a in tagA if splitNames(a)}
        if len(firsts) == 1 and splitNames(tagA[0]):
            mainArtist = splitNames(tagA[0])[0]
    # Shared albums the user approved: collabOk maps folder -> the one artist to file it under.
    collab = ov.get("collabOk", {}).get(folder)
    if isinstance(collab, str) and not albumOv:
        mainArtist = collab
    mainArtist = fix(mainArtist)
    hold = []
    albumTitle = cleanAlbumTitle(ours[0]["album"])

    # ---- year + per-track credits from MusicBrainz
    rg = mb.findReleaseGroup(albumTitle, "Various Artists" if soundtrack else mainArtist, len(ours), bool(soundtrack))
    year, mbTracks, rgCredit, releaseId = None, [], [], None
    if rg:
        year = (rg.get("first-release-date") or "")[:4] or None
        rgCredit = mb.creditNames(rg.get("artist-credit"))
        rel = mb.bestRelease(rg["id"], len(ours))
        if rel:
            releaseId = rel.get("id")
            for m in rel.get("media", []):
                for t in m.get("tracks", []):
                    credit = t.get("artist-credit") or t.get("recording", {}).get("artist-credit")
                    mbTracks.append((m.get("position", 1), t.get("position", 0), t["title"], mb.creditNames(credit)))
        notes.append(f"MusicBrainz: '{rg['title']}' by {' / '.join(rgCredit)}, first released {rg.get('first-release-date')}")
    else:
        notes.append("not found on MusicBrainz; features come from existing tags only")
    y = ov.get("years", {}).get(folder)
    if y:
        year = str(y)
        notes.append(f"year from overrides.json: {year}")
    if not year:
        year = itunesYear(mb, albumTitle, "Various Artists" if soundtrack else mainArtist)
        if year:
            notes.append(f"year from Apple's catalogue (MusicBrainz had none): {year}")
    if not year:
        hold.append("no release year found online; add one to overrides.json 'years'")
    else:
        # Safety net: the original year can't be later than a copy already tagged
        # with an earlier year, so a later year points to a reissue being matched.
        oldYears = [int(o["year"]) for o in ours if o["year"].isdigit() and 1900 < int(o["year"]) <= 2100]
        if oldYears and int(year) > min(oldYears) and not ov.get("years", {}).get(folder):
            hold.append(f"year would move LATER ({min(oldYears)} -> {year}); probably a reissue was matched")
    # An albumArtist override means the user already chose the main artist, so the
    # other credited artist just becomes a (feat. X) like on any other album.
    if not soundtrack and not albumOv and len(rgCredit) > 1 and folder not in ov.get("collabOk", {}):
        hold.append(f"album credited to several artists ({' & '.join(rgCredit)}); would use '{mainArtist}'")
    if not soundtrack and not albumOv:
        others = {key(o["artist"]) for o in ours if o["artist"].strip()} - {key(mainArtist)}
        others = {a for a in others if not a.startswith(key(mainArtist))}
        if len(others) > 3:
            hold.append(f"{len(others)} different song artists; looks like a compilation")
        elif sum(1 for o in ours if key(o["artist"]) and not key(o["artist"]).startswith(key(mainArtist))) > len(ours) / 2:
            hold.append(f"most songs are by someone other than album artist '{mainArtist}'; album artist may be wrong")

    matched = matchTracks(ours, mbTracks)
    if mbTracks and len(matched) < len(ours):
        notes.append(f"{len(ours) - len(matched)} song(s) not matched to MusicBrainz; kept their existing credits")

    tracks = []
    for i, o in enumerate(ours):
        baseTitle, titleFeats = stripFeat(o["title"])
        mbNames = matched[i][3] if i in matched else []
        if soundtrack:
            # Keep the song's whole credited artist as the lead (e.g. a duo "A & B").
            lead = fix(o["artist"]) or (mbNames[0] if mbNames else "")
            tagNames = []
            seen = {key(n) for n in splitNames(lead)} | {key(lead)}
        else:
            lead = mainArtist
            tagNames = splitNames(o["artist"]) if key(o["artist"]) != key(mainArtist) else []
            seen = {key(mainArtist)}
            if collab:
                # Co-headliners of a shared album aren't "featured" on every song.
                seen |= {key(n) for n in rgCredit} | {key(n) for n in splitNames(ours[0]["albumArtist"])}
        feats = []
        for n in mbNames + tagNames + titleFeats:
            n = fix(n)
            k = key(n)
            # Skip repeats and overlaps: "Walker" when "Walker & Royce" is credited,
            # "RÜFÜS" on a RÜFÜS DU SOL album, "070 Shake Ty Dolla Sign" mash-ups.
            if not k or k in seen or any(len(min(k, s, key=len)) >= 4 and (k in s or s in k) for s in seen):
                continue
            seen.add(k)
            feats.append(n)
        # "Redlight with Sting" -> "Redlight" when Sting is credited as a feat.
        mw = re.search(r"\s+with\s+(.+)$", baseTitle, re.I)
        if mw and feats and any(difflib.SequenceMatcher(None, key(mw.group(1)), key(n)).ratio() > 0.8 for n in feats):
            baseTitle = baseTitle[: mw.start()]

        newTitle = addFeat(baseTitle, feats)
        if soundtrack:
            newTitle = f"{lead} – {newTitle}"
        new = {
            "title": newTitle,
            "artist": soundtrack or mainArtist,
            "albumArtist": soundtrack or mainArtist,
            "album": f"{albumTitle} [{year}]" if year else ours[0]["album"],
            "year": year or o["year"],
        }
        old = {k: o[k] for k in ("title", "artist", "albumArtist", "album", "year")}
        changes = {k: v for k, v in new.items() if str(v) != str(old[k])}
        tracks.append({"path": o["path"], "old": old, "new": new, "changes": sorted(changes)})
    return {"folder": folder, "year": year, "mainArtist": soundtrack or mainArtist, "notes": notes,
            "hold": hold, "soundtrack": bool(soundtrack), "tracks": tracks,
            "releaseId": releaseId, "releaseGroupId": rg["id"] if rg else None}


def writeFiles(root, plan):
    """Write planned tags into files directly (fresh conversions only). Held albums are skipped."""
    written = 0
    for album in plan:
        if album.get("hold"):
            continue
        for tr in album["tracks"]:
            if not tr["changes"]:
                continue
            p, n = os.path.join(root, tr["path"]), tr["new"]
            if p.lower().endswith(".m4a"):
                a = MP4(p)
                a["\xa9nam"], a["\xa9ART"], a["aART"] = [n["title"]], [n["artist"]], [n["albumArtist"]]
                a["\xa9alb"], a["\xa9day"] = [n["album"]], [str(n["year"])]
                a["cpil"] = False
                a.save()
            else:
                from mutagen.id3 import TALB, TDRC, TIT2, TPE1, TPE2
                a = ID3(p)
                a.setall("TIT2", [TIT2(encoding=3, text=n["title"])])
                a.setall("TPE1", [TPE1(encoding=3, text=n["artist"])])
                a.setall("TPE2", [TPE2(encoding=3, text=n["albumArtist"])])
                a.setall("TALB", [TALB(encoding=3, text=n["album"])])
                a.setall("TDRC", [TDRC(encoding=3, text=str(n["year"]))])
                a.save()
            written += 1
            print(f"[tags set] {tr['path']}: {n['artist']} | {n['title']} | {n['album']}")
    return written


COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "artwork")
ART_MAX_PX = 600  # plenty for an iPod Classic screen, keeps files small


def hasArtwork(path):
    if path.lower().endswith(".m4a"):
        return bool((MP4(path).tags or {}).get("covr"))
    return any(k.startswith("APIC") for k in ID3(path).keys())


def findFolderImage(folder):
    """Best cover image in a download folder: a known name first, else the largest image."""
    if not folder or not os.path.isdir(folder):
        return None
    imgs = [os.path.join(folder, f) for f in os.listdir(folder)
            if not f.startswith(".") and f.lower().endswith((".jpg", ".jpeg", ".png"))]
    named = [p for p in imgs if os.path.splitext(os.path.basename(p))[0].lower() in COVER_NAMES]
    pool = named or imgs
    return max(pool, key=os.path.getsize) if pool else None


def fetchCoverArt(mb, album, tmpDir):
    """Front cover from the Cover Art Archive (MusicBrainz's artwork store)."""
    urls = []
    if album.get("releaseId"):
        urls.append(f"https://coverartarchive.org/release/{album['releaseId']}/front-500")
    if album.get("releaseGroupId"):
        urls.append(f"https://coverartarchive.org/release-group/{album['releaseGroupId']}/front-500")
    for url in urls:
        try:
            data = fetch(url, timeout=60)
            out = os.path.join(tmpDir, "caa-cover.jpg")
            open(out, "wb").write(data)
            return out
        except Exception:
            continue
    return None


def shrinkToJpeg(src, tmpDir):
    """Copy of the image as a JPEG at most ART_MAX_PX on its longest side (macOS sips)."""
    import subprocess
    out = os.path.join(tmpDir, "cover-small.jpg")
    info = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", src], capture_output=True, text=True).stdout
    sizes = [int(x) for x in re.findall(r"pixel(?:Width|Height):\s*(\d+)", info)]
    cmd = ["sips", "-s", "format", "jpeg", src, "--out", out]
    if sizes and max(sizes) > ART_MAX_PX:  # only ever shrink, never enlarge
        cmd[1:1] = ["-Z", str(ART_MAX_PX)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return out if r.returncode == 0 and os.path.exists(out) else src


def embedArtwork(root, plan, sourceRoot, mb):
    """Give every song that has no cover one: download-folder image first, else Cover Art Archive."""
    import tempfile
    from mutagen.id3 import APIC
    from mutagen.mp4 import MP4Cover
    added, missing = 0, []
    for album in plan:
        paths = [os.path.join(root, t["path"]) for t in album.get("tracks", [])]
        need = [p for p in paths if os.path.exists(p) and not hasArtwork(p)]
        if not need:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            img = findFolderImage(os.path.join(sourceRoot, album["folder"])) if sourceRoot else None
            source = "download folder"
            if not img:
                img, source = fetchCoverArt(mb, album, tmp), "Cover Art Archive"
            if not img:
                missing.append(album["folder"])
                continue
            data = open(shrinkToJpeg(img, tmp), "rb").read()
        for p in need:
            if p.lower().endswith(".m4a"):
                a = MP4(p)
                a["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
                a.save()
            else:
                a = ID3(p)
                a.setall("APIC", [APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data)])
                a.save()
            added += 1
        print(f"[artwork] {album['folder']}: {len(need)} song(s), from {source}")
    if missing:
        print(f"\nNo artwork found for {len(missing)} album(s): " + "; ".join(missing))
    return added


def findAlbumFolders(root):
    out = []
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        if any(f.lower().endswith((".m4a", ".mp3")) and not f.startswith("._") for f in fs):
            out.append(os.path.relpath(d, root))
    return sorted(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root, out, folders = args[0], args[1], args[2:]
    if "--all" in sys.argv:
        folders = findAlbumFolders(root)
    write = "--write" in sys.argv
    artSource = None
    if "--artwork" in sys.argv:
        # optional value: the folder the songs were converted FROM (to find cover.jpg etc.)
        i = sys.argv.index("--artwork")
        nxt = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        artSource = nxt if nxt and not nxt.startswith("--") else ""
        if artSource:
            folders = [f for f in folders if f != artSource] if "--all" not in sys.argv else folders
    ov = json.load(open(OVERRIDES_PATH)) if os.path.exists(OVERRIDES_PATH) else {}
    mb = MusicBrainz()
    plan = []
    try:
        for n, f in enumerate(folders, 1):
            print(f"[plan {n}/{len(folders)}] {f}", flush=True)
            try:
                plan.append(planAlbum(root, f, mb, ov))
            except Exception as e:
                print(f"   ERROR {e}", flush=True)
                plan.append({"folder": f, "tracks": [], "notes": [], "hold": [f"planning error: {e}"]})
            if n % 20 == 0:
                mb.save()
    finally:
        mb.save()
    json.dump(plan, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"plan written: {out}")
    held = [a for a in plan if a.get("hold")]
    if write:
        print(f"\nFiles updated: {writeFiles(root, plan)}")
    if artSource is not None:
        print(f"Artwork added to {embedArtwork(root, plan, artSource, mb)} song(s)")
    if held:
        print(f"\n=== {len(held)} album(s) need your review (left unchanged) ===")
        for a in held:
            print(f"  {a['folder']}: {'; '.join(a['hold'])}")


if __name__ == "__main__":
    main()
