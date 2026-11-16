#!/usr/bin/env python3
"""
Genre step for Pressing Plant (formerly runALAC), added 2026-11-16.

Rule: every artist in the LOSSLESS HD library has ONE genre. Freshly converted songs
(not in Music yet) get the genre their album artist already has in the library.
Artists the library doesn't have yet are asked about at the very end: one window
per new artist with 1-3 suggestions (Apple Music + MusicBrainz), a list of the
genres already in the library, and an empty box for a brand-new genre.

Only the converted .m4a files in the work folder are written. Songs already in
Music are never touched (their genres are the user's own picks).

Where the library's artist -> genre list comes from:
  1. Music, if it is already running with the LOSSLESS HD library open (it is never
     launched by this script, and any other library is refused);
  2. otherwise the copy saved by the last run that could read Music
     (<stateDir>/libraryGenres.json).
Answers given in the new-artist window are remembered in
<stateDir>/newArtistGenres.json, so the next album by that artist isn't asked again.

usage: genreStep.py <workDir> <stateDir>
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
from libfix import fetch, key  # same web access (SSL fix) and name matching as libfix

from mutagen.mp4 import MP4

LIBRARY_ROOT = "/Volumes/LOSSLESS HD/"

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


# Native macOS window (AppKit via JavaScript for Automation). Replaced a Tk window on
# 2026-11-16: the Tk 8.6.12 bundled with python.org Python ignores clicks on new macOS.
DIALOG_JS = r"""
ObjC.import("AppKit");
function run(argv) {
  var d = JSON.parse(argv[0]);
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  var popupIndex = 0, typed = "", warn = "";
  while (true) {
    var a = $.NSAlert.alloc.init;
    a.messageText = "New artist: " + d.artist;
    a.informativeText = (warn ? warn + "\n\n" : "") +
      "Not in your library yet. Choose the one genre this artist will use.\n" +
      "Album: " + d.albums.join("; ") + "\n\n" +
      (d.suggestions.length ? "Click a suggestion, or choose or type a genre below."
                            : "No suggestions found online. Choose or type a genre below.");
    d.suggestions.forEach(function (s) {
      a.addButtonWithTitle("Use “" + s + "”" + (d.genres.indexOf(s) < 0 ? " (new genre)" : ""));
    });
    a.addButtonWithTitle("Use the genre below");
    a.addButtonWithTitle("Skip (leave as downloaded)");

    var v = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, 380, 64));
    var l1 = $.NSTextField.labelWithString("One of my genres:");
    l1.frame = $.NSMakeRect(0, 38, 130, 20); v.addSubview(l1);
    var pop = $.NSPopUpButton.alloc.initWithFramePullsDown($.NSMakeRect(132, 33, 248, 28), false);
    pop.addItemWithTitle("— choose —");
    d.genres.forEach(function (g) { pop.addItemWithTitle(g); });
    pop.selectItemAtIndex(popupIndex);
    v.addSubview(pop);
    var l2 = $.NSTextField.labelWithString("Or a new genre:");
    l2.frame = $.NSMakeRect(0, 5, 130, 20); v.addSubview(l2);
    var field = $.NSTextField.alloc.initWithFrame($.NSMakeRect(134, 2, 244, 24));
    field.placeholderString = "type a new genre";
    field.stringValue = typed;
    v.addSubview(field);
    a.accessoryView = v;
    a.layout;

    app.activateIgnoringOtherApps(true);
    var i = a.runModal - 1000;
    if (i < d.suggestions.length) return JSON.stringify({genre: d.suggestions[i]});
    if (i > d.suggestions.length) return JSON.stringify({genre: null});
    typed = field.stringValue.js.replace(/\s+/g, " ").trim();
    popupIndex = pop.indexOfSelectedItem;
    if (typed) return JSON.stringify({genre: typed});
    if (popupIndex > 0) return JSON.stringify({genre: d.genres[popupIndex - 1]});
    warn = "Pick one of your genres or type a new one first (or press Skip).";
  }
}
"""


def musicIsRunning():
    return subprocess.run(["pgrep", "-x", "Music"], capture_output=True).returncode == 0


def readLibrary(stateDir):
    """Return {artistKey: Counter(genre)}, {artistKey: display name}, sorted genre list."""
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
                cachePath.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"Read genres from Music: {d['tracks']} songs, {len(byArtist)} artists.")
            else:
                print(f"Music's open library isn't LOSSLESS HD ({d['offDrive']} songs elsewhere); not using it.")
        else:
            print(f"Couldn't read Music: {r.stderr.strip()}")
    if data is None:
        if not cachePath.exists():
            return None
        data = json.loads(cachePath.read_text(encoding="utf-8"))
        print(f"Music not readable; using the genre list saved {data['readAt']}.")

    counts, names = collections.defaultdict(collections.Counter), {}
    for name, genres in data["artists"].items():
        k = key(name)
        counts[k].update(genres)
        names.setdefault(k, name)
    newPath = stateDir / "newArtistGenres.json"
    if newPath.exists():
        for name, g in json.loads(newPath.read_text(encoding="utf-8")).items():
            if key(name) not in counts:
                counts[key(name)][g] += 1
                names[key(name)] = name
    allGenres = sorted({g for c in counts.values() for g in c}, key=str.casefold)
    return counts, names, allGenres


def suggestGenres(artist, allGenres):
    """Up to 3 suggestions, the library's own genre names first."""
    found = []  # (source genre name, weight)

    def getJson(url):
        for attempt in range(4):
            try:
                return json.loads(fetch(url).decode("utf-8"))
            except Exception as e:
                busy = getattr(e, "code", None) in (429, 503)
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
    """Window for one new artist. Returns the chosen genre, or None to leave it as is."""
    auto = os.environ.get("GENRESTEP_AUTO")
    if auto == "skip":
        return None
    if auto == "first":
        return suggestions[0] if suggestions else None
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", DIALOG_JS,
                        json.dumps({"artist": artist, "albums": albums,
                                    "suggestions": suggestions, "genres": allGenres})],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [window failed, asking here instead] {r.stderr.strip()}")
        return askInTerminal(artist, albums, suggestions, allGenres)
    return json.loads(r.stdout).get("genre") or None


def askInTerminal(artist, albums, suggestions, allGenres):
    print(f"\nNew artist: {artist}  ({'; '.join(albums)})")
    for i, s in enumerate(suggestions, 1):
        print(f"  {i}. {s}")
    print("  Or type one of your genres, or a new one. Empty = leave as downloaded.")
    print("  Your genres: " + ", ".join(allGenres))
    a = input("> ").strip()
    if a.isdigit() and 1 <= int(a) <= len(suggestions):
        return suggestions[int(a) - 1]
    return a or None


def setGenre(paths, genre):
    for p in paths:
        f = MP4(str(p))
        f["\xa9gen"] = [genre]
        f.save()


def preview(stateDir):
    """Show the new-artist window with a sample artist. Changes nothing."""
    lib = readLibrary(stateDir)
    allGenres = lib[2] if lib else ["Alternative Rock", "Hip-Hop/Rap", "Pop"]
    picked = askGenre("Khruangbin", ["Con Todo El Mundo [2018] (preview, nothing is changed)"],
                      ["Alternative Rock", "Indie Rock", "Psychedelic Rock"], allGenres)
    print(f"Preview answer: {picked!r} (not saved)")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--preview":
        return preview(Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "Downloads" / "Lossless" / ".beetsPipeline")
    workDir, stateDir = Path(sys.argv[1]), Path(sys.argv[2])
    lib = readLibrary(stateDir)
    if lib is None:
        print("No genre list available (Music not open with LOSSLESS HD, and no saved list). "
              "Genres left as downloaded.")
        return
    counts, names, allGenres = lib

    # Group converted songs by album artist.
    byArtist = collections.defaultdict(lambda: {"name": "", "files": [], "albums": set()})
    for m4a in sorted(workDir.rglob("*.m4a")):
        tags = MP4(str(m4a)).tags or {}
        artist = ((tags.get("aART") or tags.get("\xa9ART") or [""])[0]).strip()
        if not artist:
            print(f"  [no artist] {m4a.name}: genre left as is")
            continue
        e = byArtist[key(artist)]
        e["name"] = e["name"] or artist
        e["files"].append(m4a)
        e["albums"].add((tags.get("\xa9alb") or [m4a.parent.name])[0])

    newArtists, done = [], 0
    for k, e in byArtist.items():
        if k in counts:
            genre, n = counts[k].most_common(1)[0]
            setGenre(e["files"], genre)
            done += len(e["files"])
            extra = ""
            if len(counts[k]) > 1:
                extra = f"  (library also has: {', '.join(g for g in counts[k] if g != genre)})"
            print(f"  {e['name']} -> {genre}: {len(e['files'])} songs{extra}")
        else:
            newArtists.append(e)

    remembered = {}
    newPath = stateDir / "newArtistGenres.json"
    if newPath.exists():
        remembered = json.loads(newPath.read_text(encoding="utf-8"))
    if newArtists:
        print(f"\n=== {len(newArtists)} new artist(s): asking for a genre ===")
    for e in newArtists:
        print(f"  looking up suggestions for {e['name']} ...")
        sugg = suggestGenres(e["name"], allGenres)
        genre = askGenre(e["name"], sorted(e["albums"]), sugg, allGenres)
        if not genre:
            print(f"  {e['name']}: skipped, genre left as downloaded")
            continue
        setGenre(e["files"], genre)
        done += len(e["files"])
        remembered[e["name"]] = genre
        newPath.write_text(json.dumps(remembered, ensure_ascii=False, indent=1), encoding="utf-8")
        tag = "" if genre in allGenres else " (new genre)"
        print(f"  {e['name']} -> {genre}{tag}: {len(e['files'])} songs")
    print(f"\nGenre set on {done} songs.")


if __name__ == "__main__":
    main()
