"""
MIS Music Survey - TA Table Playlist Stats
Reads a messy survey CSV (ts, track_name, artist_name, ms_played, type, genre),
cleans it, and runs a menu: top song / artist / genre, minutes listened,
Garthle (guess the mystery artist), and a signature report.

Usage:  python music_survey.py [path_to_csv]
Works on any CSV in this format - nothing about the data is hardcoded.
"""

import polars as pl
import random
import sys
from collections import Counter
from datetime import datetime

try:
    import requests
except ImportError:  # stats still work; Garthle will explain what's missing
    requests = None

# ---------- Judgment calls (documented in README) ----------
MIN_MS = 30_000            # plays under 30s don't count as "listening" (same idea as a streaming "stream")
MAX_MUSIC_MS = 30 * 60_000 # a single song "played" > 30 min is a bad entry (left running / typo)
DEFAULT_CSV = "listening_survey.csv"
API_URL = "https://www.theaudiodb.com/api/v1/json/123/search.php"
MAX_TRIES = 6

TS_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%Y",
    "%m/%d/%y %H:%M", "%d-%b-%Y %H:%M", "%b %d, %Y %I:%M %p",
]

MUSIC_WORDS = {"music", "song", "songs", "track", "tracks"}
PODCAST_WORDS = {"podcast", "podcasts", "episode", "episodes"}

GENRE_ALIASES = {
    "hip hop": "Hip-Hop", "hiphop": "Hip-Hop", "hip-hop": "Hip-Hop", "rap": "Hip-Hop",
    "kpop": "K-Pop", "k pop": "K-Pop", "k-pop": "K-Pop",
    "r&b": "R&B", "rnb": "R&B", "r and b": "R&B",
    "edm": "EDM", "alt rock": "Alt Rock", "alternative": "Alt Rock",
    "alt-rock": "Alt Rock", "alternative rock": "Alt Rock",
    "r & b": "R&B", "pop music": "Pop", "country music": "Country",
}


# =====================================================================
# PASS 1 - READ & CLEAN
# =====================================================================
def norm_key(text):
    """Case/space-insensitive key so 'DRAKE ', 'drake', 'Drake' group together."""
    return " ".join(str(text).split()).casefold()


def parse_ts(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return None


def parse_ms(raw):
    try:
        value = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:  # NaN or negative
        return None
    return int(value)


def clean_genre(raw):
    g = " ".join((raw or "").split())
    if not g or g.casefold() in {"n/a", "na", "none", "null", "unknown", "?", "-"}:
        return "Unknown"
    return GENRE_ALIASES.get(g.casefold(), g.title())


def load_and_clean(path):
    """Read the survey with Polars and return clean rows plus a cleaning report."""
    report = Counter()
    rows, seen = [], set()
    df = pl.read_csv(
        path,
        null_values=["", "NA", "N/A", "null", "NULL"],
        infer_schema=False,
        encoding="utf8-lossy",
    )
    df.columns = [column.strip().lower() for column in df.columns]
    needed = {"ts", "track_name", "artist_name", "ms_played", "type", "genre"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError("CSV is missing columns: " + ", ".join(sorted(missing)))

    report["rows read"] = df.height
    for raw in df.to_dicts():
        track = " ".join((raw.get("track_name") or "").split())
        artist = " ".join((raw.get("artist_name") or "").split())
        ms = parse_ms(raw.get("ms_played"))
        ts = parse_ts(raw.get("ts"))
        kind_raw = (raw.get("type") or "").strip().casefold()
        if not track or not artist:
            report["dropped: missing track/artist"] += 1
            continue
        if ms is None:
            report["dropped: bad ms_played"] += 1
            continue
        if ts is None:
            report["dropped: bad/missing timestamp"] += 1
            continue
        if kind_raw in MUSIC_WORDS:
            kind = "music"
        elif kind_raw in PODCAST_WORDS:
            kind = "podcast"
        else:
            report["dropped: unrecognized type"] += 1
            continue
        if ms < MIN_MS:
            report["dropped: played under 30 seconds"] += 1
            continue
        if kind == "music" and ms > MAX_MUSIC_MS:
            report["dropped: impossible song length"] += 1
            continue
        if ts > datetime.now():
            report["flagged (kept): timestamp in the future"] += 1

        dedupe = (ts, norm_key(track), norm_key(artist), ms)
        if dedupe in seen:
            report["dropped: duplicate row"] += 1
            continue
        seen.add(dedupe)
        rows.append({
            "ts": ts, "track": track, "artist": artist, "ms": ms,
            "type": kind, "genre": clean_genre(raw.get("genre")),
            "track_key": (norm_key(track), norm_key(artist)),
            "artist_key": norm_key(artist),
        })

    artist_names, track_names = {}, {}
    for key, group in _group(rows, "artist_key", "artist").items():
        artist_names[key] = Counter(group).most_common(1)[0][0]
    for key, group in _group(rows, "track_key", "track").items():
        track_names[key] = Counter(group).most_common(1)[0][0]
    for row in rows:
        row["artist"] = artist_names[row["artist_key"]]
        row["track"] = track_names[row["track_key"]]
    return rows, report


def _group(rows, key_field, value_field):
    out = {}
    for r in rows:
        out.setdefault(r[key_field], []).append(r[value_field])
    return out


def print_clean_report(report, rows):
    print("\n--- Data cleaning summary ---")
    print(f"Rows read:          {report['rows read']}")
    for reason, n in sorted(report.items()):
        if reason.startswith(("dropped", "flagged")):
            print(f"  {reason}: {n}")
    print(f"Rows that survived: {len(rows)}\n")


# =====================================================================
# PASS 2 - STATS
# =====================================================================
def music_only(rows):
    return [r for r in rows if r["type"] == "music"]


def show_top(title, rows, key_field, label_of, skip=None):
    """Rank by number of plays. Ties are broken by total minutes listened, and reported as ties."""
    counts, ms_total, labels = Counter(), Counter(), {}
    for r in rows:
        if skip and r[skip[0]] == skip[1]:
            continue
        counts[r[key_field]] += 1
        ms_total[r[key_field]] += r["ms"]
        labels[r[key_field]] = label_of(r)
    if not counts:
        print("No data to report.")
        return
    ranked = sorted(counts, key=lambda k: (-counts[k], -ms_total[k], labels[k]))
    best = counts[ranked[0]]
    tied = [k for k in ranked if counts[k] == best]
    print(f"\n{title}: {labels[ranked[0]]}  ({best} plays, {ms_total[ranked[0]] / 60000:.1f} min)")
    if len(tied) > 1:
        print(f"  Tie on plays between {len(tied)} - broken by minutes listened. Also tied:")
        for k in tied[1:6]:
            print(f"    {labels[k]} ({ms_total[k] / 60000:.1f} min)")
        if len(tied) > 6:
            print(f"    ...and {len(tied) - 6} more")
    print("Top 5:")
    for i, k in enumerate(ranked[:5], 1):
        print(f"  {i}. {labels[k]} - {counts[k]} plays")


def show_minutes(rows):
    music_ms = sum(r["ms"] for r in rows if r["type"] == "music")
    pod_ms = sum(r["ms"] for r in rows if r["type"] == "podcast")
    print(f"\nMinutes of music listened:   {music_ms / 60000:,.1f}  ({music_ms / 3_600_000:,.1f} hours)")
    print(f"Minutes of podcasts (extra): {pod_ms / 60000:,.1f}")
    print("(Music stats exclude podcasts and plays under 30 seconds.)")


# =====================================================================
# PASS 3 - GARTHLE
# =====================================================================
def lookup_artist(name):
    """Return a profile dict, or None if the API had no match or the network failed."""
    if requests is None:
        print("The 'requests' package is missing. Run: pip install requests")
        return None
    try:
        r = requests.get(API_URL, params={"s": name}, timeout=8)
        r.raise_for_status()
        artists = r.json().get("artists")
        if not artists:
            return None
        a = artists[0]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None

    try:
        formed = int(a.get("intFormedYear"))
    except (TypeError, ValueError):
        formed = None
    try:
        members = int(a.get("intMembers"))
        members_label = "Band" if members > 1 else "Solo"
    except (TypeError, ValueError):
        members_label = "?"
    return {
        "name": a.get("strArtist") or name,
        "Formed": formed,
        "Members": members_label,
        "Genre": (a.get("strGenre") or "?").strip() or "?",
        "Gender": (a.get("strGender") or "?").strip() or "?",
        "Country": (a.get("strCountryCode") or "?").strip() or "?",
    }


def score_guess(guess, secret):
    """Print one clue row. Returns True if every attribute matches."""
    parts = []
    for attr in ("Formed", "Members", "Genre", "Gender", "Country"):
        g, s = guess[attr], secret[attr]
        same = (g == s) if attr == "Formed" else str(g).casefold() == str(s).casefold()
        shown = g if g is not None else "?"
        if same:
            parts.append(f"[GREEN {attr}: {shown}]")
        elif attr == "Formed" and g is not None and s is not None:
            hint = "later" if s > g else "earlier"
            parts.append(f"[gray {attr}: {shown} -> go {hint}]")
        else:
            parts.append(f"[gray {attr}: {shown}]")
    print("  " + "  ".join(parts))
    return guess["name"].casefold() == secret["name"].casefold()


def pick_secret(rows):
    """Pick a random artist from the survey that the API actually knows."""
    names = sorted({r["artist"] for r in music_only(rows)})
    random.shuffle(names)
    for name in names[:15]:
        profile = lookup_artist(name)
        if profile and profile["Formed"] is not None:
            return profile
    return None


def play_garthle(rows):
    print("\n=== GARTHLE: guess the mystery artist ===")
    print(f"You have {MAX_TRIES} tries. Green = match, gray = no. The year tells you earlier/later.")
    print("Type 'quit' to give up.\n")
    secret = pick_secret(rows)
    if secret is None:
        print("Couldn't reach the artist database (or no artists matched). Check your internet and try again.")
        return
    tries, guessed = 0, set()
    while tries < MAX_TRIES:
        name = input(f"Guess {tries + 1}/{MAX_TRIES}: ").strip()
        if not name:
            print("  Please type an artist name.")
            continue
        if name.casefold() == "quit":
            break
        if name.casefold() in guessed:
            print("  You already guessed that one (no try used).")
            continue
        guess = lookup_artist(name)
        if guess is None:
            print("  Couldn't find that artist (or the lookup failed). No try used - ask again.")
            continue
        guessed.add(name.casefold())
        tries += 1
        if score_guess(guess, secret):
            print(f"\nYou got it in {tries}! It was {secret['name']}.")
            return
    print(f"\nOut of tries. The mystery artist was {secret['name']}.")


# =====================================================================
# PASS 4 - SIGNATURE REPORT: "Listening Personality"
# =====================================================================
DAYPARTS = [("Late night (12a-5a)", 0, 5), ("Morning (5a-12p)", 5, 12),
            ("Afternoon (12p-5p)", 12, 17), ("Evening (5p-9p)", 17, 21),
            ("Night (9p-12a)", 21, 24)]


def signature_report(rows):
    music = music_only(rows)
    if not music:
        print("No music data for the report.")
        return
    print("\n=== LISTENING PERSONALITY REPORT ===")

    # 1. When does the room listen?
    by_part = Counter()
    for r in music:
        h = r["ts"].hour
        for label, lo, hi in DAYPARTS:
            if lo <= h < hi:
                by_part[label] += 1
    total = len(music)
    biggest = max(by_part.values())
    print("\nWhen the room listens:")
    for label, _, _ in DAYPARTS:
        n = by_part[label]
        bar = "#" * round(30 * n / biggest)
        print(f"  {label:<20} {bar:<30} {n} ({100 * n / total:.0f}%)")
    peak = max(range(24), key=lambda h: sum(1 for r in music if r["ts"].hour == h))
    print(f"  Busiest hour: {peak % 12 or 12}{'am' if peak < 12 else 'pm'}")

    # 2. Variety vs. loyalty
    artists = Counter(r["artist"] for r in music)
    top_artist, top_n = artists.most_common(1)[0]
    share = 100 * top_n / total
    variety = len(artists) / total
    print(f"\nLoyalty: {top_artist} makes up {share:.0f}% of all plays.")
    print(f"Variety: {len(artists)} different artists across {total} plays.")

    # 3. Personality label
    night = by_part["Night (9p-12a)"] + by_part["Late night (12a-5a)"]
    if night / total > 0.4:
        owl = "Night owl"
    elif by_part["Morning (5a-12p)"] / total > 0.4:
        owl = "Early bird"
    else:
        owl = "All-day listener"
    taste = "explorer" if variety > 0.5 else ("superfan crowd" if share > 25 else "balanced crowd")
    print(f"\nVerdict: {owl} + {taste}.")

    # 4. Longest single listen
    longest = max(music, key=lambda r: r["ms"])
    print(f"Longest single play: '{longest['track']}' by {longest['artist']} "
          f"({longest['ms'] / 60000:.1f} min)")


# =====================================================================
# MENU
# =====================================================================
MENU = """
===== TA TABLE PLAYLIST =====
 1) Top song
 2) Top artist
 3) Top genre
 4) Minutes listened
 5) Garthle (guess the artist)
 6) Signature report
 7) Quit
"""


# Load and clean the file before showing the menu. An optional command-line
# argument lets the program work with any CSV that uses the required columns.
path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
try:
    rows, report = load_and_clean(path)
except FileNotFoundError:
    print(f"Couldn't find '{path}'. Put the survey CSV next to this script or pass its path.")
    rows, report = [], Counter()
except pl.exceptions.PolarsError as error:
    print(f"Couldn't read the CSV file: {error}")
    rows, report = [], Counter()
except ValueError as error:
    print(f"Couldn't use the CSV file: {error}")
    rows, report = [], Counter()

if rows:
    print_clean_report(report, rows)
    music = music_only(rows)
    choice = ""
    while choice != "7":
        print(MENU)
        choice = input("Pick 1-7: ").strip()
        if choice == "1":
            show_top("Top song", music, "track_key", lambda r: f"{r['track']} - {r['artist']}")
        elif choice == "2":
            show_top("Top artist", music, "artist_key", lambda r: r["artist"])
        elif choice == "3":
            show_top("Top genre", music, "genre", lambda r: r["genre"], skip=("genre", "Unknown"))
        elif choice == "4":
            show_minutes(rows)
        elif choice == "5":
            play_garthle(rows)
        elif choice == "6":
            signature_report(rows)
        elif choice == "7":
            print("Thanks for listening!")
        else:
            print("Please enter a number from 1 to 7.")
else:
    print("No usable rows were loaded, so the menu cannot run.")
