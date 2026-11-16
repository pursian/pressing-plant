#!/usr/bin/env python3
"""
Genre step for Pressing Plant (formerly runALAC), added 2026-11-16.

Rule: every artist in the LOSSLESS HD library has ONE genre. Freshly converted songs
(not in Music yet) get the genre their album artist already has in the library.
Artists the library doesn't have yet are asked about at the very end: one window
per new artist with
  * the artist name, editable: cut a combined credit ("Calvin Harris & Ellie
    Goulding") down to the main artist, and everyone else goes into each song's
    title as "(feat. ...)" (the album's tag rules are re-run with that artist);
  * 1-3 genre suggestions (Apple Music + MusicBrainz) as buttons;
  * a list of the genres already in the library, and an empty box for a new genre.

Only the converted files in the work folder are written. Songs already in Music
are never touched (their genres are the user's own picks).

Where the library's artist -> genre list comes from:
  1. Music, if it is already running with the LOSSLESS HD library open (it is never
     launched by this script, and any other library is refused);
  2. otherwise the copy saved by the last run that could read Music
     (<stateDir>/libraryGenres.json).
Remembered between runs, in <stateDir>:
  newArtistGenres.json  genre picked for each new artist
  artistRenames.json    artist names edited in the window (old credit -> main artist)

usage: genreStep.py <workDir> <stateDir> [<inputDir>]
       genreStep.py --preview      show the new-artist window with a sample; changes nothing
  GENRESTEP_AUTO=first|skip  answer new-artist windows automatically (testing only)
"""
import collections
import difflib
import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import libfix  # same web access (SSL fix), name matching and tag rules as the tag step
from libfix import fetch, key, splitNames, stripFeat, addFeat

from mutagen.mp4 import MP4

LIBRARY_ROOT = "/Volumes/LOSSLESS HD/"
TITLE, ARTIST, ALBUM_ARTIST, ALBUM, GENRE = "\xa9nam", "\xa9ART", "aART", "\xa9alb", "\xa9gen"

READ_MUSIC_JS = r"""
var m = Application("Music");
var t = m.libraryPlaylists[0].fileTracks;
var loc = t.location(), off = 0, n = loc.length;
for (var i = 0; i < n; i++) {
  var p = loc[i] ? loc[i].toString() : "";
  if (p && p.indexOf("%ROOT%") !== 0) off++;
}
JSON.stringify({tracks: n, offDrive: off, artist: t.artist(), albumArtist: t.albumArtist(), genre: t.genre()});
""".replace("%ROOT%", LIBRARY_ROOT)

# Native macOS window (AppKit via JavaScript for Automation). Replaced a Tk window on
# 2026-11-16: the Tk 8.6.12 bundled with python.org Python ignores clicks on new macOS.
DIALOG_JS = r"""
ObjC.import("AppKit");
function run(argv) {
  var d = JSON.parse(argv[0]);
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  var popupIndex = 0, typed = "", artist = d.artist, warn = "";
  function label(text, x, y, w, small) {
    // small = a short grey hint line across the whole width
    var l = $.NSTextField.labelWithString(text);
    l.frame = $.NSMakeRect(x, y, w, small ? 16 : 20);
    if (small) { l.font = $.NSFont.systemFontOfSize(11); l.textColor = $.NSColor.secondaryLabelColor; }
    return l;
  }
  while (true) {
    var a = $.NSAlert.alloc.init;
    a.messageText = "New artist: " + d.artist;
    a.informativeText = (warn ? warn + "\n\n" : "") +
      "Not in your library yet.\nAlbum: " + d.albums.join("; ") + "\n\n" +
      "Check the artist name, then pick this artist's one genre: click a suggestion, " +
      "or choose from your genres / type a new one and press “Save with the genre from the list or box”.";
    d.suggestions.forEach(function (s) {
      a.addButtonWithTitle("Use “" + s + "”" + (d.genres.indexOf(s) < 0 ? " (new genre)" : ""));
    });
    a.addButtonWithTitle("Save with the genre from the list or box");
    a.addButtonWithTitle("Skip (change nothing)");

    var v = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, 420, 138));
    v.addSubview(label("Artist name:", 0, 117, 130));
    var nameBox = $.NSTextField.alloc.initWithFrame($.NSMakeRect(134, 114, 264, 24));
    nameBox.stringValue = artist;
    v.addSubview(nameBox);
    v.addSubview(label("Edit to the main artist; anyone removed becomes (feat. …).", 0, 86, 420, true));
    v.addSubview(label("Leave both names for a real joint album.", 0, 72, 420, true));
    v.addSubview(label("One of my genres:", 0, 46, 130));
    var pop = $.NSPopUpButton.alloc.initWithFramePullsDown($.NSMakeRect(132, 41, 268, 28), false);
    pop.addItemWithTitle("— choose —");
    d.genres.forEach(function (g) { pop.addItemWithTitle(g); });
    pop.selectItemAtIndex(popupIndex);
    v.addSubview(pop);
    v.addSubview(label("Or a new genre:", 0, 9, 130));
    var field = $.NSTextField.alloc.initWithFrame($.NSMakeRect(134, 6, 264, 24));
    field.placeholderString = "type a new genre";
    field.stringValue = typed;
    v.addSubview(field);
    a.accessoryView = v;
    a.layout;

    app.activateIgnoringOtherApps(true);
    var i = a.runModal - 1000;
    if (i > d.suggestions.length) return JSON.stringify({genre: null, artist: null});
    artist = nameBox.stringValue.js.replace(/\s+/g, " ").trim();
    typed = field.stringValue.js.replace(/\s+/g, " ").trim();
    popupIndex = pop.indexOfSelectedItem;
    if (!artist) { artist = d.artist; warn = "The artist name can't be empty."; continue; }
    if (i < d.suggestions.length) return JSON.stringify({genre: d.suggestions[i], artist: artist});
    if (typed) return JSON.stringify({genre: typed, artist: artist});
    if (popupIndex > 0) return JSON.stringify({genre: d.genres[popupIndex - 1], artist: artist});
    warn = "Choose one of your genres or type a new one first (or press Skip).";
  }
}
"""

# Common outside names for genres -> the library's own wording, when it has that genre.
ALIASES = {
    "hip hop": "Hip-Hop/Rap", "hip-hop": "Hip-Hop/Rap", "rap": "Hip-Hop/Rap", "trap": "Hip-Hop/Rap",
    "rnb": "R&B", "r&b/soul": "R&B", "contemporary r&b": "R&B", "rhythm and blues": "R&B",
    "electronica": "Electronic", "edm": "Dance", "electro house": "House", "deep house": "House",
    "alternative": "Alternative Rock", "alternative rock": "Alternative Rock", "indie": "Indie Rock",
    "rock": "Classic Rock", "metal": "Heavy Metal", "singer/songwriter": "Soft Rock",
    "progressive": "Progressive Rock", "psychedelic": "Psychedelic Rock", "dnb": "Drum And Bass",
    "drum and bass": "Drum And Bass", "drum & bass": "Drum And Bass", "soundtrack": "Soundtrack",
    "latin pop": "Latin", "reggaeton": "Latin", "musica mexicana": "Latin", "jazz": "Jazz",
}


def loadJson(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def saveJson(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def musicIsRunning():
    return subprocess.run(["pgrep", "-x", "Music"], capture_output=True).returncode == 0


def readLibrary(stateDir):
    """Return {artistKey: Counter(genre)}, sorted genre list."""
    cachePath = stateDir / "libraryGenres.json"
    data = None
    if musicIsRunning():
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", READ_MUSIC_JS],
                           capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            d = json.loads(r.stdout)
            if d["tracks"] and d["offDrive"] == 0:
                byArtist = collections.defaultdict(collections.Counter)
                for aa, a, g in zip(d["albumArtist"], d["artist"], d["genre"]):
                    name = (aa or a or "").strip()
                    if name and g.strip():
                        byArtist[name][g.strip()] += 1
                data = {"readAt": time.strftime("%Y-%m-%d %H:%M"), "tracks": d["tracks"],
                        "artists": {k: dict(v) for k, v in byArtist.items()}}
                saveJson(cachePath, data)
                print(f"Read genres from Music: {d['tracks']} songs, {len(byArtist)} artists.")
            else:
                print(f"Music's open library isn't LOSSLESS HD ({d['offDrive']} songs elsewhere); not using it.")
        else:
            print(f"Couldn't read Music: {r.stderr.strip()}")
    if data is None:
        if not cachePath.exists():
            return None
        data = loadJson(cachePath)
        print(f"Music not readable; using the genre list saved {data['readAt']}.")

    counts = collections.defaultdict(collections.Counter)
    for name, genres in data["artists"].items():
        counts[key(name)].update(genres)
    for name, g in loadJson(stateDir / "newArtistGenres.json").items():
        if key(name) not in counts:
            counts[key(name)][g] += 1
    allGenres = sorted({g for c in counts.values() for g in c}, key=str.casefold)
    return counts, allGenres


def suggestGenres(artist, allGenres):
    """Up to 3 suggestions, the library's own genre names first."""
    found = []  # (source genre name, weight)

    def getJson(url):
        for attempt in range(4):
            try:
                return json.loads(fetch(url).decode("utf-8"))
            except Exception as e:
                busy = getattr(e, "code", None) in (429, 503) or "timed out" in str(e)
                if busy and attempt < 3:
                    time.sleep(3 + attempt * 3)  # MusicBrainz/Apple asking us to slow down
                    continue
                print(f"  [lookup error] {e}", file=sys.stderr)
                return {}

    q = urllib.parse.urlencode({"term": artist, "entity": "musicArtist", "limit": 10})
    for res in getJson("https://itunes.apple.com/search?" + q).get("results", []):
        if key(res.get("artistName")) == key(artist) and res.get("primaryGenreName"):
            found.append((res["primaryGenreName"], 5))
            break
    time.sleep(3)  # Apple allows roughly 20 requests a minute
    q = urllib.parse.urlencode({"term": artist, "entity": "album", "limit": 25})
    for res in getJson("https://itunes.apple.com/search?" + q).get("results", []):
        if key(res.get("artistName")) == key(artist) and res.get("primaryGenreName"):
            found.append((res["primaryGenreName"], 1))

    time.sleep(1)
    q = urllib.parse.urlencode({"query": f'artist:"{artist}"', "fmt": "json", "limit": 3})
    hits = [a for a in getJson("https://musicbrainz.org/ws/2/artist/?" + q).get("artists", [])
            if a.get("score", 0) >= 90 and key(a.get("name")) == key(artist)]
    if hits:
        time.sleep(1)
        d = getJson(f"https://musicbrainz.org/ws/2/artist/{hits[0]['id']}?inc=genres&fmt=json")
        for g in sorted(d.get("genres", []), key=lambda g: -g.get("count", 0))[:5]:
            found.append((g["name"], 2 + min(g.get("count", 0), 3)))

    lib = {g.casefold(): g for g in allGenres}

    def toLibrary(name):
        n = name.casefold().strip()
        if n in lib:
            return lib[n]
        alias = ALIASES.get(n)
        if alias and alias.casefold() in lib:
            return lib[alias.casefold()]
        close = difflib.get_close_matches(n, list(lib), n=1, cutoff=0.85)
        return lib[close[0]] if close else None

    scoreLib, scoreNew = collections.Counter(), collections.Counter()
    for name, w in found:
        mapped = toLibrary(name)
        if mapped:
            scoreLib[mapped] += w
        else:
            scoreNew[name.title() if name.islower() else name] += w
    out = [g for g, _ in scoreLib.most_common()] + [g for g, _ in scoreNew.most_common()]
    return out[:3]


def askGenre(artist, albums, suggestions, allGenres):
    """Window for one new artist. Returns (genre, artist name); (None, None) = skip."""
    auto = os.environ.get("GENRESTEP_AUTO")
    if auto == "skip":
        return None, None
    if auto == "first":
        return (suggestions[0] if suggestions else None), artist
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", DIALOG_JS,
                        json.dumps({"artist": artist, "albums": albums,
                                    "suggestions": suggestions, "genres": allGenres})],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [window failed, asking here instead] {r.stderr.strip()}")
        return askInTerminal(artist, albums, suggestions, allGenres)
    d = json.loads(r.stdout)
    return d.get("genre") or None, d.get("artist") or None


def askInTerminal(artist, albums, suggestions, allGenres):
    print(f"\nNew artist: {artist}  ({'; '.join(albums)})")
    name = input(f"  Artist name [{artist}]: ").strip() or artist
    for i, s in enumerate(suggestions, 1):
        print(f"  {i}. {s}")
    print("  Or type one of your genres, or a new one. Empty = skip.")
    print("  Your genres: " + ", ".join(allGenres))
    a = input("> ").strip()
    if a.isdigit() and 1 <= int(a) <= len(suggestions):
        return suggestions[int(a) - 1], name
    return (a or None), (name if a else None)


def setGenre(paths, genre):
    for p in paths:
        f = MP4(str(p))
        f[GENRE] = [genre]
        f.save()


def renameArtist(workDir, inputDir, folders, files, newName):
    """
    Make newName the album artist of these albums and move everyone else credited
    into each song's title as (feat. ...). Re-runs libfix's tag rules with newName as
    the album artist (so year, [YYYY] and features come out like any other album);
    if libfix still can't settle an album, just rewrites artist + feat. on its songs.
    """
    ov = {}
    if os.path.exists(libfix.OVERRIDES_PATH):
        ov = json.load(open(libfix.OVERRIDES_PATH))
    ov.setdefault("albumArtist", {})
    mb = libfix.MusicBrainz()
    plans = []
    try:
        for folder in folders:
            ov["albumArtist"][folder] = newName
            plan = libfix.planAlbum(str(workDir), folder, mb, ov)
            if plan.get("hold"):
                print(f"  {folder}: tag rules still unsure ({'; '.join(plan['hold'])}); setting artist + feat. only")
                fixFeatOnly([f for f in files if f.parent == workDir / folder], newName)
            else:
                plans.append(plan)
    finally:
        mb.save()
    libfix.writeFiles(str(workDir), plans)
    if plans and inputDir:
        libfix.embedArtwork(str(workDir), plans, str(inputDir), mb)


def fixFeatOnly(paths, newName):
    for p in paths:
        f = MP4(str(p))
        t = f.tags
        credited = splitNames((t.get(ARTIST) or [""])[0]) + splitNames((t.get(ALBUM_ARTIST) or [""])[0])
        title, already = stripFeat((t.get(TITLE) or [p.stem])[0])
        guests, seen = [], {key(newName)}
        for n in already + credited:
            if key(n) not in seen:
                seen.add(key(n))
                guests.append(n)
        newTitle = addFeat(title, guests)
        t[TITLE], t[ARTIST], t[ALBUM_ARTIST] = [newTitle], [newName], [newName]
        f.save()
        print(f"[tags set] {p.name}: {newName} | {newTitle}")


def readAlbumArtists(workDir):
    byArtist = collections.defaultdict(lambda: {"name": "", "files": [], "albums": set(), "folders": set()})
    for m4a in sorted(workDir.rglob("*.m4a")):
        tags = MP4(str(m4a)).tags or {}
        artist = ((tags.get(ALBUM_ARTIST) or tags.get(ARTIST) or [""])[0]).strip()
        if not artist:
            print(f"  [no artist] {m4a.name}: genre left as is")
            continue
        e = byArtist[key(artist)]
        e["name"] = e["name"] or artist
        e["files"].append(m4a)
        e["albums"].add((tags.get(ALBUM) or [m4a.parent.name])[0])
        e["folders"].add(str(m4a.parent.relative_to(workDir)))
    return byArtist


def preview(stateDir):
    """Show the new-artist window with a sample artist. Changes nothing."""
    lib = readLibrary(stateDir)
    allGenres = lib[1] if lib else ["Alternative Rock", "Hip-Hop/Rap", "Pop"]
    genre, name = askGenre("Calvin Harris & Ellie Goulding", ["Motion (preview, nothing is changed)"],
                           ["Dance", "Electronic", "Dance Pop"], allGenres)
    print(f"Preview answer: genre={genre!r}, artist={name!r} (not saved)")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--preview":
        return preview(Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "Downloads" / "Lossless" / ".beetsPipeline")
    workDir, stateDir = Path(sys.argv[1]), Path(sys.argv[2])
    inputDir = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    lib = readLibrary(stateDir)
    if lib is None:
        print("No genre list available (Music not open with LOSSLESS HD, and no saved list). "
              "Genres left as downloaded.")
        return
    counts, allGenres = lib
    remembered = loadJson(stateDir / "newArtistGenres.json")
    renamesPath = stateDir / "artistRenames.json"
    renames = loadJson(renamesPath)

    # Artist names edited in an earlier window: apply them again first.
    byArtist = readAlbumArtists(workDir)
    renamedAny = False
    for k, e in byArtist.items():
        target = next((new for old, new in renames.items() if key(old) == k), None)
        if target:
            print(f"  {e['name']} -> artist '{target}' (your earlier edit)")
            renameArtist(workDir, inputDir, sorted(e["folders"]), e["files"], target)
            renamedAny = True
    if renamedAny:
        byArtist = readAlbumArtists(workDir)

    newArtists, done = [], 0
    for k, e in byArtist.items():
        if k in counts:
            genre = counts[k].most_common(1)[0][0]
            setGenre(e["files"], genre)
            done += len(e["files"])
            extra = ""
            if len(counts[k]) > 1:
                extra = f"  (library also has: {', '.join(g for g in counts[k] if g != genre)})"
            print(f"  {e['name']} -> {genre}: {len(e['files'])} songs{extra}")
        else:
            newArtists.append(e)

    if newArtists:
        print(f"\n=== {len(newArtists)} new artist(s): asking for a genre ===")
    for e in newArtists:
        k = key(e["name"])
        if k in counts:  # answered earlier in this run (e.g. a renamed album became this artist)
            genre = counts[k].most_common(1)[0][0]
            setGenre(e["files"], genre)
            done += len(e["files"])
            print(f"  {e['name']} -> {genre}: {len(e['files'])} songs (as answered above)")
            continue
        print(f"  looking up suggestions for {e['name']} ...")
        sugg = suggestGenres(e["name"], allGenres)
        genre, name = askGenre(e["name"], sorted(e["albums"]), sugg, allGenres)
        if not genre:
            print(f"  {e['name']}: skipped, left as downloaded")
            continue
        if name and key(name) != key(e["name"]):
            print(f"  artist name edited: '{e['name']}' -> '{name}'")
            renameArtist(workDir, inputDir, sorted(e["folders"]), e["files"], name)
            renames[e["name"]] = name
            saveJson(renamesPath, renames)
            if key(name) in counts:
                libGenre = counts[key(name)].most_common(1)[0][0]
                if libGenre != genre:
                    print(f"  {name} is already in your library as {libGenre}; using that (one genre per artist)")
                genre = libGenre
        else:
            name = e["name"]
        setGenre(e["files"], genre)
        done += len(e["files"])
        if key(name) not in counts:
            remembered[name] = genre
            saveJson(stateDir / "newArtistGenres.json", remembered)
            counts[key(name)][genre] += 1
        tag = "" if genre in allGenres else " (new genre)"
        print(f"  {name} -> {genre}{tag}: {len(e['files'])} songs")
    print(f"\nGenre set on {done} songs.")


if __name__ == "__main__":
    main()
