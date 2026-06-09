"""
build_all.py — generate the full NBA Iron Man Streaks static site.

Outputs:
  index.html / regular.html / combined.html   (leaderboards + featured stats)
  reasons.html                                 (index of absence reasons)
  reasons/<reason-slug>.html                   (one page per reason)
  players/<slug>.html                          (one page per player)

Streaks recomputed from PlayerStatistics.csv + Games.csv (same logic as
streak_analysis.py): Playoffs use calendar-year seasons with NO tenure window;
Regular/Combined use NBA-season-start-year seasons WITH a tenure window. The
timeline shows the FULL team schedule (game 1 -> last game) for every season a
player was rostered.

Usage:
  python build_all.py            # skip player pages that already exist
  python build_all.py --force    # regenerate everything
"""

import os
import re
import sys
import json
import html
import unicodedata
from datetime import date
from collections import defaultdict, Counter

import pandas as pd

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #
BASE_DIR = r"C:\nba-streaks"
DATA_DIR = os.path.join(BASE_DIR, "data")
GAMES_CSV = os.path.join(DATA_DIR, "Games.csv")
PLAYERSTATS_CSV = os.path.join(DATA_DIR, "PlayerStatistics.csv")
PLAYERS_META_CSV = os.path.join(DATA_DIR, "Players.csv")
NATIONALITIES_CSV = os.path.join(DATA_DIR, "nationalities.csv")
PLAYERS_DIR = os.path.join(BASE_DIR, "players")
REASONS_DIR = os.path.join(BASE_DIR, "reasons")

REGULAR = "Regular Season"
PLAYOFFS = "Playoffs"
UNIVERSE = {REGULAR, PLAYOFFS}
MIN_IRON = 5

# "Active" = streak/absence reaches into the current 2025-26 season.
ACTIVE_STR = "2025-10-01"
ACTIVE_DATE = date(2025, 10, 1)

SUBTITLE = "Longest consecutive games played, 1947–present"
esc = lambda s: html.escape(str(s), quote=True)

TYPES = ["playoff", "regular", "combined"]
TYPE_META = {
    "playoff":  ("Playoff", "Playoffs", "../index.html"),
    "regular":  ("Regular Season", "Regular Season", "../regular.html"),
    "combined": ("Combined", "Combined", "../combined.html"),
}
BADGE_TEXT = {"playoff": "Playoffs Iron Man", "regular": "Regular Season Iron Man",
              "combined": "Combined Iron Man"}

# #6: a missed season only renders if its reason looks like injury/illness.
INJURY_KW = ["injur", "illness", "surgery", "sprain", "strain", "torn", "fracture",
             "broken", "knee", "ankle", "back", "shoulder", "hip", "hamstring",
             "achilles", "acl", "mcl", "wrist", "hand", "foot", "calf", "groin",
             "concussion", "bruise", "contusion", "tendinitis", "tendonitis", "plantar"]
# Reasons that are NOT injury/illness even if a body-part word sneaks in.
INJURY_EXCLUDE = ["coach", "g league", "g-league", "gleague", "d league", "d-league",
                  "personal", "suspen", "trade", "not with team", "rest"]


# --------------------------------------------------------------------------- #
# Reason normalization + glossary
# --------------------------------------------------------------------------- #
BODY = {"ankle", "ankles", "knee", "knees", "foot", "feet", "hand", "hands",
        "wrist", "shoulder", "hip", "hips", "groin", "hamstring", "hamstrings",
        "calf", "thigh", "elbow", "back", "toe", "finger", "thumb", "quad",
        "quadriceps", "achilles", "neck", "rib", "ribs", "abdomen", "abductor",
        "adductor", "oblique", "heel", "shin", "forearm", "bicep", "biceps",
        "tricep", "triceps", "pelvis", "jaw", "nose", "eye", "head", "chest",
        "glute", "glutes", "leg", "arm"}
ACRONYMS = {"acl", "mcl", "pcl", "lcl"}
WORD_EXPAND = {"rt": "Right", "lt": "Left", "sprd": "Sprained",
               "str": "Strain", "sore": "Soreness"}


def normalize_reason(raw):
    if raw is None:
        return "—"
    s = str(raw).strip()
    if not s:
        return "—"
    s = s.rstrip(". ").strip()
    if not s:
        return "—"
    prefix, rest = None, s
    m = re.match(r"^(DNP|DND|NWT)\b[\s:\-]*(.*)$", s, re.IGNORECASE)
    if m:
        prefix = m.group(1).upper()
        rest = m.group(2).strip()
    if rest == "":
        return {"DNP": "Did Not Play", "DND": "Did Not Dress",
                "NWT": "Not With Team"}.get(prefix, "—")
    low = rest.lower()
    if low in ("cd", "coach's decision", "coachs decision", "coaches decision"):
        return "Coach's Decision"
    if low in ("injury/illness", "injury / illness"):
        return "Injury"
    if "g league" in low or "g-league" in low or low == "gleague":
        return "G League Assignment"
    if "suspen" in low:
        return "Suspension"
    if low in ("personal", "personal reasons"):
        return "Personal Reasons"
    if low in ("rest", "dnp - rest"):
        return "Rest"
    tokens = re.split(r"\s+", rest)
    out = []
    for i, tok in enumerate(tokens):
        t = tok.strip(",.")
        tl = t.lower()
        nxt = tokens[i + 1].lower().strip(",.") if i + 1 < len(tokens) else ""
        if not t:
            continue
        if tl in WORD_EXPAND:
            out.append(WORD_EXPAND[tl])
        elif tl in ("l", "r") and nxt in BODY:
            out.append("Left" if tl == "l" else "Right")
        elif tl in ACRONYMS:
            out.append(t.upper())
        else:
            out.append(t[:1].upper() + t[1:].lower())
    res = " ".join(out).strip()
    res = res.replace("Injury/illness", "Injury").replace("Injury/Illness", "Injury")
    return res if res else "—"


def reason_slug(reason):
    s = unicodedata.normalize("NFKD", reason.lower()).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def is_injury(reason):
    rl = reason.lower()
    if any(kw in rl for kw in INJURY_EXCLUDE):
        return False
    return any(kw in rl for kw in INJURY_KW)


GLOSSARY_CANDIDATES = [
    ("dnp", "DNP", "Did Not Play"),
    ("dnd", "DND", "Did Not Dress — on the active roster but did not suit up (often injury)"),
    ("nwt", "NWT", "Not With Team — not traveling/dressed with the club (injury, personal, suspension, G League)"),
    ("coach", "Coach's Decision", "Healthy scratch — the coach chose not to play them"),
    ("injury/illness", "Injury / Illness", "Out with an injury or illness"),
    ("rt", "Rt", "Right (side of the body)"),
    ("lt", "Lt", "Left (side of the body)"),
    ("sore", "Sore", "Soreness"),
    ("sprain", "Sprained", "A stretched or torn ligament"),
    ("strain", "Strain", "A stretched or torn muscle or tendon"),
    ("suspen", "Suspension", "Suspended by the league or team"),
    ("rest", "Rest", "Rested (load management)"),
    ("g league", "G League", "Assigned to the G League"),
]


def build_glossary_html(corpus_lower):
    rows = []
    for trigger, term, meaning in GLOSSARY_CANDIDATES:
        if any(trigger in c for c in corpus_lower):
            rows.append(f"<tr><td><b>{esc(term)}</b></td><td>{esc(meaning)}</td></tr>")
    if not rows:
        return "<p>No coded reasons found.</p>"
    return ('<table class="dtable"><thead><tr><th>Code</th><th>Meaning</th></tr>'
            '</thead><tbody>' + "".join(rows) + "</tbody></table>")


# --------------------------------------------------------------------------- #
# Country flags + nationality override
# --------------------------------------------------------------------------- #
COUNTRY_ISO = {
    "USA": "US", "United States": "US", "United States of America": "US", "US": "US",
    "Canada": "CA", "France": "FR", "Serbia": "RS", "Australia": "AU", "Croatia": "HR",
    "Spain": "ES", "Brazil": "BR", "Lithuania": "LT", "Argentina": "AR", "Germany": "DE",
    "Senegal": "SN", "Turkey": "TR", "Türkiye": "TR", "Slovenia": "SI", "Nigeria": "NG",
    "Greece": "GR", "Italy": "IT", "Russia": "RU", "Ukraine": "UA", "Puerto Rico": "PR",
    "United Kingdom": "GB", "England": "GB", "Great Britain": "GB", "Georgia": "GE",
    "Latvia": "LV", "Montenegro": "ME", "Bosnia and Herzegovina": "BA",
    "Bosnia & Herzegovina": "BA", "Bosnia": "BA", "Dominican Republic": "DO",
    "Cameroon": "CM", "Switzerland": "CH", "Mexico": "MX", "China": "CN", "Japan": "JP",
    "Israel": "IL", "Czech Republic": "CZ", "Czechia": "CZ", "Poland": "PL",
    "Netherlands": "NL", "Sweden": "SE", "Finland": "FI", "Austria": "AT", "Belgium": "BE",
    "Angola": "AO", "Sudan": "SD", "South Sudan": "SS",
    "Democratic Republic of the Congo": "CD", "Republic of the Congo": "CG", "Congo": "CG",
    "Mali": "ML", "Ivory Coast": "CI", "Tunisia": "TN", "Egypt": "EG", "Morocco": "MA",
    "New Zealand": "NZ", "Jamaica": "JM", "Bahamas": "BS", "Trinidad and Tobago": "TT",
    "Venezuela": "VE", "Colombia": "CO", "Panama": "PA", "Haiti": "HT", "Cape Verde": "CV",
    "Cabo Verde": "CV", "Guinea": "GN", "Gabon": "GA", "Ghana": "GH", "Iran": "IR",
    "Lebanon": "LB", "Philippines": "PH", "South Korea": "KR", "Korea": "KR", "India": "IN",
    "Estonia": "EE", "Hungary": "HU", "Romania": "RO", "Bulgaria": "BG", "Slovakia": "SK",
    "Portugal": "PT", "Ireland": "IE", "Norway": "NO", "Denmark": "DK", "Uruguay": "UY",
    "Macedonia": "MK", "North Macedonia": "MK", "Kazakhstan": "KZ",
    "U.S. Virgin Islands": "VI", "US Virgin Islands": "VI", "Virgin Islands": "VI",
}


# Hardcoded nationality fixes that outrank both nationalities.csv and Players.csv.
# Keyed by normname(full name). Ndudi Ebi is listed as Great Britain / USA in the
# source data but represented Nigeria.
NATIONALITY_OVERRIDE = {
    "tim duncan": "USA",      # listed as US Virgin Islands; show the US flag
    "ndudi ebi": "Nigeria",   # listed as Great Britain / USA; represented Nigeria
}


def normname(s):
    s = unicodedata.normalize("NFKD", str(s).lower()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def country_to_flag(country):
    """Return (iso2_lower, country_name) for the flagcdn image, or None."""
    if not country:
        return None
    c = str(country).strip()
    iso = COUNTRY_ISO.get(c) or COUNTRY_ISO.get(c.title())
    return (iso.lower(), c) if iso else None


def load_players_meta():
    """Return (country_by_pid, birthyear_by_pid, available)."""
    if not os.path.exists(PLAYERS_META_CSV):
        return {}, {}, False
    df = pd.read_csv(PLAYERS_META_CSV, usecols=["personId", "country", "birthDate"],
                     low_memory=False)
    country, birth = {}, {}
    for r in df.itertuples(index=False):
        pid = int(r.personId)
        if isinstance(r.country, str) and r.country.strip():
            country[pid] = r.country.strip()
        bd = str(r.birthDate) if r.birthDate is not None else ""
        m = re.match(r"(\d{4})", bd)
        if m:
            birth[pid] = int(m.group(1))
    return country, birth, True


def load_nationalities():
    """Return name->nationality from data/nationalities.csv (PLAYER, NATIONALITY)."""
    if not os.path.exists(NATIONALITIES_CSV):
        return {}, False
    df = pd.read_csv(NATIONALITIES_CSV, low_memory=False)
    cols = {c.lower(): c for c in df.columns}
    pcol = cols.get("player")
    ncol = cols.get("nationality")
    if not pcol or not ncol:
        print("nationalities.csv missing PLAYER/NATIONALITY columns — ignoring.")
        return {}, False
    lookup = {}
    for r in df[[pcol, ncol]].itertuples(index=False):
        name, nat = r[0], r[1]
        if isinstance(name, str) and isinstance(nat, str) and name.strip() and nat.strip():
            lookup[normname(name)] = nat.strip()
    return lookup, True


def build_flags(names, country_by_pid, nationalities):
    """pid -> flag emoji, using nationalities.csv override then Players.csv country."""
    flags = {}
    overridden = 0
    for pid, (first, last) in names.items():
        key = normname(f"{first} {last}")
        nat = NATIONALITY_OVERRIDE.get(key) or nationalities.get(key)
        if nat:
            overridden += 1
            country = nat
        else:
            country = country_by_pid.get(pid)
        f = country_to_flag(country)
        if f:
            flags[pid] = f
    return flags, overridden


# --------------------------------------------------------------------------- #
# Load games / players
# --------------------------------------------------------------------------- #
def nba_season(d):
    return d.year if d.month >= 7 else d.year - 1


def slugify(first, last, pid):
    s = f"{first} {last}".lower().replace("'", "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return f"{s}-{pid}"


def load_games():
    df = pd.read_csv(
        GAMES_CSV,
        usecols=["gameId", "gameDate", "gameType", "hometeamId", "hometeamCity",
                 "hometeamName", "awayteamId", "awayteamCity", "awayteamName"],
        low_memory=False,
    )
    df = df[df["gameType"].isin(UNIVERSE)].copy()
    df["gameId"] = df["gameId"].astype(str)
    df["gameDate"] = pd.to_datetime(df["gameDate"], errors="coerce")
    df = df.dropna(subset=["gameDate", "hometeamId", "awayteamId"])
    game_info = {}
    ts_pf, ts_reg, ts_comb = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in df.itertuples(index=False):
        gid = r.gameId
        d = r.gameDate.date()
        gt = r.gameType
        hid, aid = int(r.hometeamId), int(r.awayteamId)
        game_info[gid] = (d, gt, hid, r.hometeamCity, r.hometeamName,
                          aid, r.awayteamCity, r.awayteamName)
        nk = nba_season(d)
        ts_comb[(hid, nk)].append((d, gid))
        ts_comb[(aid, nk)].append((d, gid))
        if gt == PLAYOFFS:
            yr = d.year
            ts_pf[(hid, yr)].append((d, gid))
            ts_pf[(aid, yr)].append((d, gid))
        else:
            ts_reg[(hid, nk)].append((d, gid))
            ts_reg[(aid, nk)].append((d, gid))
    for store in (ts_pf, ts_reg, ts_comb):
        for k in store:
            store[k].sort()
    print(f"Loaded {len(game_info)} games.")
    return game_info, ts_pf, ts_reg, ts_comb


def load_players(game_info):
    cols = ["firstName", "lastName", "personId", "gameId", "playerteamId",
            "gameType", "numMinutes", "points", "assists", "reboundsTotal", "comment"]
    df = pd.read_csv(PLAYERSTATS_CSV, usecols=cols, low_memory=False)
    df = df[df["gameType"].isin(UNIVERSE)].copy()
    df["gameId"] = df["gameId"].astype(str)
    df = df.dropna(subset=["playerteamId"])
    df["numMinutes"] = pd.to_numeric(df["numMinutes"], errors="coerce").fillna(0.0)
    for c in ("points", "assists", "reboundsTotal"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["comment"] = df["comment"].fillna("").astype(str)
    games, names, appeared = {}, {}, defaultdict(set)
    for r in df.itertuples(index=False):
        gid = r.gameId
        if gid not in game_info:
            continue
        pid = int(r.personId)
        if pid not in names:
            names[pid] = (r.firstName, r.lastName)
        mins = float(r.numMinutes)
        games.setdefault(pid, {})[gid] = (
            int(r.playerteamId), mins, r.comment,
            float(r.points), float(r.assists), float(r.reboundsTotal), r.gameType,
        )
        if mins > 0:
            appeared[pid].add(gid)
    print(f"Loaded rows for {len(games)} players.")
    return games, names, appeared


# --------------------------------------------------------------------------- #
# Streak machinery
# --------------------------------------------------------------------------- #
def team_city_name(game_info, gid, team_id):
    info = game_info[gid]
    if team_id == info[2]:
        return info[3], info[4]
    return info[6], info[7]


def opponent_and_home(game_info, gid, team_id):
    info = game_info[gid]
    if team_id == info[2]:
        return info[6], info[7], True
    return info[3], info[4], False


def parts_windows(prows, game_info, season_of, gt_ok):
    parts, win = set(), {}
    for gid, row in prows.items():
        if not gt_ok(row[6]):
            continue
        d = game_info[gid][0]
        tid = row[0]
        s = season_of(d)
        parts.add((s, tid))
        key = (s, tid)
        w = win.get(key)
        if w is None:
            win[key] = [d, d]
        else:
            if d < w[0]:
                w[0] = d
            if d > w[1]:
                w[1] = d
    return parts, win


def build_sequence(parts, win, teamseason, windowed):
    seq = {}
    for (s, tid) in parts:
        for (d, gid) in teamseason.get((tid, s), ()):
            if windowed:
                lo, hi = win[(s, tid)]
                if not (lo <= d <= hi):
                    continue
            seq[gid] = d
    return sorted(seq.items(), key=lambda kv: (kv[1], kv[0]))


def walk_runs(seq, appeared_set):
    app_runs, abs_runs = [], []
    run, run_app = [], None
    for gid, date_ in seq:
        a = gid in appeared_set
        if run and a != run_app:
            (app_runs if run_app else abs_runs).append(run)
            run = []
        run_app = a
        run.append((date_, gid))
    if run:
        (app_runs if run_app else abs_runs).append(run)
    return app_runs, abs_runs


def absence_record(run, prows, game_info):
    reasons, teams = [], Counter()
    for _, gid in run:
        row = prows.get(gid)
        if row:
            if row[2]:
                reasons.append(row[2])
            c, n = team_city_name(game_info, gid, row[0])
            teams[f"{c} {n}".strip()] += 1
    reason = normalize_reason(Counter(reasons).most_common(1)[0][0]) if reasons else "—"
    team = teams.most_common(1)[0][0] if teams else ""
    frm, to = run[0][0], run[-1][0]
    return {"count": len(run), "days": (to - frm).days, "frm": frm, "to": to,
            "reason": reason, "rslug": reason_slug(reason), "team": team}


def analyze(pid, full, games, appeared, game_info, ts_pf, ts_reg, ts_comb, tracked=None):
    prows = games[pid]
    app = appeared.get(pid, set())

    pf_parts, pf_win = parts_windows(prows, game_info, lambda d: d.year,
                                     lambda gt: gt == PLAYOFFS)
    reg_parts, reg_win = parts_windows(prows, game_info, nba_season, lambda gt: gt == REGULAR)
    comb_parts, comb_win = parts_windows(prows, game_info, nba_season, lambda gt: True)

    pf_app, _ = walk_runs(build_sequence(pf_parts, pf_win, ts_pf, False), app)
    reg_app, _ = walk_runs(build_sequence(reg_parts, reg_win, ts_reg, True), app)
    comb_app, comb_abs = walk_runs(build_sequence(comb_parts, comb_win, ts_comb, True), app)

    def best(runs):
        if not runs:
            return {"len": 0, "start": None, "end": None, "team": ("", "")}
        r = max(runs, key=len)
        tid = prows[r[-1][1]][0]
        return {"len": len(r), "start": r[0][0], "end": r[-1][0],
                "team": team_city_name(game_info, r[-1][1], tid)}

    streaks = {"playoff": best(pf_app), "regular": best(reg_app), "combined": best(comb_app)}

    # #3 unified iron-man streaks from the COMBINED sequence (no Type column)
    iron = []
    for r in comb_app:
        if len(r) >= MIN_IRON:
            tid = prows[r[-1][1]][0]
            c, n = team_city_name(game_info, r[-1][1], tid)
            iron.append({"games": len(r), "start": r[0][0], "end": r[-1][0],
                         "team": f"{c} {n}".strip()})
    iron.sort(key=lambda x: x["games"], reverse=True)

    # #4 unified absences from the COMBINED sequence (no Type column)
    absences = [absence_record(run, prows, game_info) for run in comb_abs]
    absences.sort(key=lambda a: (a["count"], a["days"]), reverse=True)

    res = {"streaks": streaks, "iron": iron, "absences": absences}
    if full:
        res["missed"] = missed_seasons(prows, app, game_info, ts_comb, tracked)
        res["timeline"] = build_timeline(prows, app, game_info, ts_comb)
    return res


def missed_seasons(prows, app, game_info, ts_comb, tracked_seasons=None):
    season_app = defaultdict(int)
    season_team_rows = defaultdict(lambda: defaultdict(int))
    season_comments = defaultdict(list)
    for gid, row in prows.items():
        d = game_info[gid][0]
        s = nba_season(d)
        if row[1] > 0:
            season_app[s] += 1
        season_team_rows[s][row[0]] += 1
        if row[1] == 0 and row[2]:
            season_comments[s].append(row[2])

    out = []
    for s, teams in season_team_rows.items():
        if season_app.get(s, 0) != 0:
            continue
        if tracked_seasons is not None and s not in tracked_seasons:
            continue  # pre-1951: minutes weren't tracked
        if not season_comments[s]:
            continue
        reason = normalize_reason(Counter(season_comments[s]).most_common(1)[0][0])
        if not is_injury(reason):  # #11: injury/illness only
            continue
        tid = max(teams, key=teams.get)
        team_games = ts_comb.get((tid, s), [])
        if not team_games:
            continue
        city, name = team_city_name(game_info, team_games[0][1], tid)
        out.append({"label": f"{s}-{(s + 1) % 100:02d}", "team": f"{city} {name}".strip(),
                    "games": len(team_games), "reason": reason, "rslug": reason_slug(reason)})
    out.sort(key=lambda m: m["label"])
    return out


def build_timeline(prows, app, game_info, ts_comb):
    """#2: show the FULL team schedule (game 1 -> last) for every season the
    player was rostered. Games before the player's first appearance/row show as
    red (team played, player wasn't there)."""
    roster = set()
    for gid, row in prows.items():
        roster.add((nba_season(game_info[gid][0]), row[0]))
    rows = []
    for (s, tid) in roster:
        allg = list(ts_comb.get((tid, s), ()))
        reg = sorted([(d, g) for d, g in allg if game_info[g][1] == REGULAR])
        pf = sorted([(d, g) for d, g in allg if game_info[g][1] == PLAYOFFS])
        if reg:
            rows.append(_trow(s, "reg", tid, reg, prows, app, game_info))
        if pf:
            rows.append(_trow(s, "pf", tid, pf, prows, app, game_info))
    rows.sort(key=lambda r: r["sort"])
    return rows


def _trow(season, kind, tid, games, prows, app, game_info):
    city, name = team_city_name(game_info, games[0][1], tid)
    label = f"{season}-{(season + 1) % 100:02d}" if kind == "reg" else f"{season + 1} Playoffs"
    squares = []
    for (d, gid) in games:
        opp_city, opp_name, home = opponent_and_home(game_info, gid, tid)
        vs = "vs" if home else "@"
        row = prows.get(gid)
        if gid in app:
            cls = "g"
            _, mins, _, pts, ast, reb, _ = row
            detail = f"{int(round(mins))} min · {int(pts)} pts · {int(reb)} reb · {int(ast)} ast"
        elif row is not None and row[2]:
            cls = "d"
            detail = f"DNP: {normalize_reason(row[2])}"
        elif row is not None:
            cls = "r"
            detail = "Did not play (0 min)"
        else:
            cls = "r"
            detail = "Missed (team played, no record for player)"
        squares.append((cls, f"{d.isoformat()} · {vs} {opp_city} {opp_name} · {detail}"))
    return {"sort": games[0][0], "label": label, "team": f"{city} {name}", "squares": squares}


# --------------------------------------------------------------------------- #
# HTML scaffolding
# --------------------------------------------------------------------------- #
CSS = """
:root{
--bg:#f5f5f7;--surface:#fff;--surface-hover:#f0f0f2;--border:#d1d1d6;
--text:#1d1d1f;--muted:#6e6e73;--accent:#3b82f6;--accent-dim:rgba(59,130,246,.15);
--green:#34c759;--red:#ef4444;--gray:#c7c7cc;
--green-txt:#1d8a40;--red-txt:#d12c2c;--orange:#b26b00;}
*{box-sizing:border-box;}html,body{margin:0;padding:0;}
body{background:var(--bg);color:var(--text);line-height:1.5;-webkit-font-smoothing:antialiased;
font-family:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
a{color:var(--accent);text-decoration:none;}
.mono,.num{font-family:'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums;}
/* flagcdn PNG flag images (render on every OS, unlike flag emoji on Windows) */
.flag{vertical-align:middle;border-radius:2px;box-shadow:0 0 0 .5px rgba(0,0,0,.18);margin-left:.05rem;}

/* nav tabs */
.nav{display:flex;gap:.3rem;flex-wrap:wrap;max-width:1100px;margin:0 auto;padding:1.5rem 1.5rem .3rem;}
.nav a{font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:600;padding:.4rem .8rem;
border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--muted);text-decoration:none;}
.nav a:hover{border-color:var(--accent);color:var(--accent);}
.nav a.active{background:var(--accent);border-color:var(--accent);color:#fff;}

.wrap{max-width:1100px;margin:0 auto;padding:.5rem 1.5rem 4rem;}
header{margin-bottom:1rem;}
.brand{font-family:'JetBrains Mono',monospace;font-size:.7rem;color:var(--muted);
text-transform:uppercase;letter-spacing:.08em;display:block;margin-bottom:.3rem;}
h1{font-size:clamp(1.4rem,4vw,1.8rem);font-weight:700;letter-spacing:-.03em;margin:0 0 .3rem;
display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;}
h1 .accent{color:var(--accent);}
h2{font-size:1.15rem;font-weight:700;letter-spacing:-.025em;margin:2rem 0 .7rem;padding-bottom:.4rem;
border-bottom:2px solid var(--text);color:var(--text);display:flex;align-items:baseline;gap:.5rem;flex-wrap:wrap;}
h3{font-size:.95rem;font-weight:700;margin:1.2rem 0 .5rem;}
.subtitle{color:var(--muted);font-size:.9rem;font-weight:400;margin:0;max-width:60rem;}
.backtop{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:.78rem;
color:var(--muted);margin:0 0 1rem;text-decoration:none;}
.backtop:hover{color:var(--accent);}

.controls{display:flex;flex-wrap:wrap;align-items:center;gap:.6rem;margin:.6rem 0;}
.search{flex:1 1 100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);
font-size:.9rem;font-family:inherit;padding:.6rem .8rem;outline:none;transition:.15s;}
.search::placeholder{color:var(--muted);}
.search:hover{border-color:var(--accent);}
.search:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-dim);}
.count{color:var(--muted);font-size:.74rem;}
.count b{color:var(--text);font-weight:600;}

.table-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;}
.table-card.scroll{max-height:560px;overflow:auto;}
table.board{width:100%;border-collapse:collapse;font-size:.86rem;}
table.board thead th{position:sticky;top:0;background:var(--surface-hover);color:var(--muted);text-align:left;
font-weight:600;font-size:.66rem;letter-spacing:.04em;text-transform:uppercase;padding:.7rem .8rem;
cursor:pointer;user-select:none;white-space:nowrap;border-bottom:1px solid var(--border);z-index:2;}
table.board thead th:hover,table.board thead th.active{color:var(--accent);}
table.board thead th .arrow{color:var(--accent);font-size:.6rem;margin-left:.2rem;}
th.col-streak,td.col-streak{text-align:right;}
table.board tbody td{padding:.55rem .8rem;border-bottom:1px solid var(--border);white-space:nowrap;
font-family:'JetBrains Mono',monospace;font-weight:500;font-variant-numeric:tabular-nums;}
table.board tbody td.col-player{font-family:'DM Sans',sans-serif;}
table.board tbody tr:last-child td{border-bottom:none;}
table.board tbody tr:hover{background:var(--surface-hover);}
.col-rank{color:var(--muted);font-variant-numeric:tabular-nums;width:3.2rem;font-size:.78rem;}
.col-streak{font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums;}
.col-date{color:var(--muted);font-variant-numeric:tabular-nums;}
.plink{color:var(--text);text-decoration:none;font-weight:600;}
.plink:hover{color:var(--accent);}
.empty{padding:2.5rem 1rem;text-align:center;color:var(--muted);}

.badges{display:flex;flex-wrap:wrap;gap:.5rem;margin:.7rem 0 .2rem;}
.badge{font-family:'JetBrains Mono',monospace;padding:.4rem .7rem;border-radius:8px;font-weight:500;
font-size:.72rem;border:1px solid var(--border);background:var(--surface);color:var(--muted);}
.badge.on{background:var(--accent-dim);border-color:var(--accent);color:var(--text);}
.badge.on b{color:var(--accent);}
.badge.off{opacity:.65;}

.legend{display:flex;gap:1rem;flex-wrap:wrap;color:var(--muted);font-size:.78rem;margin:.4rem 0 .9rem;}
.legend span{display:inline-flex;align-items:center;gap:.4rem;}
.sq{display:inline-block;width:11px;height:11px;margin:1.5px;border-radius:2px;vertical-align:middle;}
.sq.g{background:var(--green);}.sq.r{background:var(--red);}.sq.d{background:var(--gray);}
.tlcard{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.4rem 1rem;}
.trow{display:flex;gap:14px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--border);}
.trow:last-child{border-bottom:none;}
.tlabel{width:170px;flex:none;}
.tseason{display:block;font-weight:700;font-size:.86rem;}
.tteam{display:block;color:var(--muted);font-size:.74rem;font-family:'JetBrains Mono',monospace;}
.tsquares{flex:1;min-width:0;line-height:1;}

.dtable{width:100%;border-collapse:collapse;font-size:.84rem;}
.dtable thead th{text-align:left;color:var(--muted);font-size:.66rem;text-transform:uppercase;letter-spacing:.04em;
font-weight:600;padding:.6rem .8rem;border-bottom:1px solid var(--border);cursor:pointer;user-select:none;
background:var(--surface-hover);position:sticky;top:0;}
.dtable thead th.active{color:var(--accent);}
.dtable thead th .arrow{color:var(--accent);font-size:.6rem;margin-left:.2rem;}
.dtable td{padding:.5rem .8rem;border-bottom:1px solid var(--border);}
.dtable tbody tr:last-child td{border-bottom:none;}
.dtable tbody tr:hover{background:var(--surface-hover);}
.dtable .num{font-variant-numeric:tabular-nums;font-family:'JetBrains Mono',monospace;}
.showall{margin-top:.7rem;background:none;color:var(--accent);border:none;font-family:'JetBrains Mono',monospace;
font-size:.74rem;cursor:pointer;}
.showall:hover{text-decoration:underline;}
.gbtn{background:none;border:1px solid var(--border);color:var(--muted);border-radius:50%;width:20px;height:20px;
cursor:pointer;font-size:.7rem;line-height:1;padding:0;}
.gbtn:hover{color:var(--accent);border-color:var(--accent);}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.4);display:none;align-items:center;justify-content:center;z-index:50;padding:20px;}
.modal.open{display:flex;}
.modal-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;max-width:560px;width:100%;max-height:80vh;overflow:auto;padding:1.4rem;}
.simgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.6rem;margin-top:.4rem;}
.simcard{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:.7rem .8rem;text-decoration:none;color:var(--text);display:block;}
.simcard:hover{border-color:var(--accent);}
.simcard .nm{font-weight:600;font-size:.84rem;}
.simcard .st{color:var(--accent);font-weight:700;font-size:.76rem;margin-top:.25rem;font-family:'JetBrains Mono',monospace;}
.reasonblock{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.9rem 1.1rem;margin-bottom:.9rem;}
.reasonblock h2{margin:0 0 .3rem;font-size:1rem;text-transform:none;letter-spacing:-.01em;border-bottom:none;padding-bottom:0;color:var(--text);}
.reasonblock h2 a{color:var(--text);text-decoration:none;}
.reasonblock h2 a:hover{color:var(--accent);}
.rcount{color:var(--muted);font-size:.72rem;font-weight:500;font-family:'JetBrains Mono',monospace;}
.foot{text-align:center;font-size:.72rem;color:var(--muted);margin-top:1.4rem;font-family:'JetBrains Mono',monospace;line-height:1.7;}
@media(max-width:560px){.wrap{padding:.5rem 1rem 3rem;}.nav{padding:1rem 1rem .3rem;}.trow{flex-direction:column;gap:4px;}.tlabel{width:auto;}}
"""


def nav(prefix, active):
    items = [("regular.html", "Regular Season", "regular"),
             ("index.html", "Playoffs", "playoff"),
             ("combined.html", "Combined", "combined"),
             ("reasons.html", "Absence Reasons", "reasons")]
    parts = []
    for href, label, key in items:
        cls = ' class="active"' if key == active else ''
        parts.append(f'<a href="{prefix}{href}"{cls}>{label}</a>')
    return f'<nav class="nav">{"".join(parts)}</nav>'


def page(title, body, scripts=""):
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{title}</title>\n"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
        "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>\n"
        "<link href=\"https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap\" rel=\"stylesheet\">\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n{body}\n{scripts}\n</body>\n</html>\n"
    )


def flag_html(flag):
    # flag is (iso2_lower, country_name) or "" — render a flagcdn PNG image so
    # flags display on every OS (Windows can't render flag emoji).
    if not flag:
        return ""
    iso2, country = flag
    return (f' <img src="https://flagcdn.com/24x18/{iso2}.png" width="24" height="18" '
            f'alt="{esc(country)}" class="flag">')


def name_with_flag(name, flag):
    return esc(name) + flag_html(flag)


# --------------------------------------------------------------------------- #
# Leaderboard pages (with featured stats)
# --------------------------------------------------------------------------- #
LEADERBOARD_JS = r"""
<script>
const DATA = __DATA__;
const NUMERIC = new Set([0,2]); const DATES = new Set([3,4]);
let sortKey = 0, sortAsc = true;
const tbody=document.getElementById('tbody'),countEl=document.getElementById('count'),
headers=Array.from(document.querySelectorAll('#board thead th')),
boxes=Array.from(document.querySelectorAll('.search'));
function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function compare(a,b,k){let av=a[k],bv=b[k];if(NUMERIC.has(k)){av=+av;bv=+bv;}else if(DATES.has(k)){av=av||'';bv=bv||'';}else{av=String(av).toLowerCase();bv=String(bv).toLowerCase();}return av<bv?-1:av>bv?1:0;}
function render(){
  const q=(boxes[0].value||'').trim().toLowerCase();
  let rows=q?DATA.filter(r=>r[1].toLowerCase().includes(q)):DATA.slice();
  rows.sort((a,b)=>{const c=compare(a,b,sortKey);return sortAsc?c:-c;});
  if(!rows.length){tbody.innerHTML='<tr><td class="empty" colspan="5">No players match “'+escapeHtml(boxes[0].value)+'”</td></tr>';}
  else{const f=[];for(const r of rows){f.push('<tr><td class="col-rank">'+r[0]+'</td><td class="col-player"><a class="plink" href="players/'+r[5]+'.html">'+escapeHtml(r[1])+'</a>'+(r[6]?' <img src="https://flagcdn.com/24x18/'+r[6][0]+'.png" width="24" height="18" alt="'+escapeHtml(r[6][1])+'" class="flag">':'')+'</td><td class="col-streak">'+r[2]+'</td><td class="col-date">'+r[3]+'</td><td class="col-date">'+r[4]+'</td></tr>');}tbody.innerHTML=f.join('');}
  countEl.innerHTML='<b>'+rows.length+'</b> of '+DATA.length+' players';
  headers.forEach(h=>{const k=+h.dataset.key;h.classList.toggle('active',k===sortKey);const e=h.querySelector('.arrow');if(e)e.remove();if(k===sortKey){const s=document.createElement('span');s.className='arrow';s.textContent=sortAsc?'▲':'▼';h.appendChild(s);}});
}
headers.forEach(h=>h.addEventListener('click',()=>{const k=+h.dataset.key;if(k===sortKey){sortAsc=!sortAsc;}else{sortKey=k;sortAsc=!(NUMERIC.has(k)||DATES.has(k));}render();}));
boxes.forEach(b=>b.addEventListener('input',()=>{boxes.forEach(o=>{if(o!==b)o.value=b.value;});render();}));
render();
</script>
"""


def search_box(with_count=True):
    cnt = '<div class="count" id="count"></div>' if with_count else ''
    return ('<div class="controls"><input class="search" type="search" '
            'placeholder="Search the all-time leaderboard by player name…" '
            f'autocomplete="off" spellcheck="false">{cnt}</div>')


def streak_section(title, rows):
    """#5 static streak table: rank, player, streak, start, end."""
    if not rows:
        return f'<h2>{title}</h2><p class="subtitle">No active streaks reaching into 2025-26.</p>'
    body = "".join(
        f'<tr><td class="col-rank num">{i}</td>'
        f'<td class="col-player"><a class="plink" href="players/{r["slug"]}.html">{esc(r["name"])}</a>'
        f'{flag_html(r["flag"])}</td>'
        f'<td class="num col-streak">{r["len"]}</td>'
        f'<td class="num col-date">{r["start"]}</td>'
        f'<td class="num col-date">{r["end"]}</td></tr>'
        for i, r in enumerate(rows, start=1)
    )
    return (f'<h2>{title}</h2><div class="table-card scroll">'
            '<table class="board"><thead><tr><th class="col-rank">#</th><th>Player</th>'
            '<th class="col-streak">Streak</th><th>Start</th><th>End</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def absence_section(title, rows, summaries, empty_msg):
    """#5 static absence table: rank, player, games missed, date range, reason."""
    if not rows:
        return f'<h2>{title}</h2><p class="subtitle">{empty_msg}</p>'
    body = "".join(
        f'<tr><td class="col-rank num">{i}</td>'
        f'<td class="col-player"><a class="plink" href="players/{summaries[a["pid"]]["slug"]}.html">'
        f'{esc(summaries[a["pid"]]["name"])}</a>{flag_html(summaries[a["pid"]]["flag"])}</td>'
        f'<td class="num">{a["count"]}</td>'
        f'<td class="num">{a["frm"].isoformat()} → {a["to"].isoformat()}</td>'
        f'<td><a class="plink" href="reasons/{a["rslug"]}.html">{esc(a["reason"])}</a></td></tr>'
        for i, a in enumerate(rows, start=1)
    )
    return (f'<h2>{title}</h2><div class="table-card scroll">'
            '<table class="board"><thead><tr><th class="col-rank">#</th><th>Player</th>'
            '<th class="col-streak">Games Missed</th><th>Date Range</th><th>Reason</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def leaderboard_page(title_html, board_sorted, abs_sorted, summaries, active):
    """#5 four sections: (1) Top 25 Active Streaks, (2) All-Time Leaderboard
    (top 250, searchable), (3) Top 25 Active Absences, (4) Longest Absences
    All-Time (top 250). Search boxes top & bottom filter section 2 only."""
    active_streaks = [r for r in board_sorted if r["end"] and r["end"] > ACTIVE_STR][:25]
    active_abs = [a for a in abs_sorted if a["to"] >= ACTIVE_DATE][:25]
    alltime_abs = abs_sorted[:250]
    top250 = board_sorted[:250]
    data_rows = [[idx + 1, r["name"], r["len"], r["start"], r["end"], r["slug"], r["flag"]]
                 for idx, r in enumerate(top250)]

    board_tbl = (
        "<div class=\"table-card scroll\"><table class=\"board\" id=\"board\"><thead><tr>"
        "<th data-key=\"0\" class=\"col-rank\">Rank</th>"
        "<th data-key=\"1\">Player</th>"
        "<th data-key=\"2\" class=\"col-streak\">Streak (games)</th>"
        "<th data-key=\"3\">Start Date</th>"
        "<th data-key=\"4\">End Date</th>"
        "</tr></thead><tbody id=\"tbody\"></tbody></table></div>"
    )

    body = (
        f"{nav('', active)}\n<div class=\"wrap\">\n"
        f"<header><span class=\"brand\">HoopsHype · NBA Iron Man</span>"
        f"<h1>{title_html}</h1><p class=\"subtitle\">{SUBTITLE}</p></header>\n"
        f"{streak_section('Top 25 Active Streaks', active_streaks)}\n"
        f"<h2>All-Time Leaderboard</h2>\n{search_box(True)}\n{board_tbl}\n{search_box(False)}\n"
        f"{absence_section('Top 25 Active Absences', active_abs, summaries, 'No absences reaching into 2025-26.')}\n"
        f"{absence_section('Longest Absences All-Time', alltime_abs, summaries, 'No absences on record.')}\n"
        "</div>"
    )
    scripts = LEADERBOARD_JS.replace("__DATA__", json.dumps(data_rows, separators=(",", ":"), ensure_ascii=False))
    return page(re.sub("<[^>]+>", "", title_html), body, scripts)


# --------------------------------------------------------------------------- #
# Player page
# --------------------------------------------------------------------------- #
def table_showall(sec_id, headers, row_cells, top=10):
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = []
    for i, cells in enumerate(row_cells):
        if i >= top:
            body.append(f'<tr class="{sec_id}-x" style="display:none">{cells}</tr>')
        else:
            body.append(f"<tr>{cells}</tr>")
    tbl = (f'<div class="table-card"><table class="dtable"><thead>{head}</thead>'
           f'<tbody>{"".join(body)}</tbody></table></div>')
    if len(row_cells) > top:
        tbl += (f'<button class="showall" data-sec="{sec_id}" onclick="showAll(this)">'
                f'Show all {len(row_cells)}</button>')
    return tbl


PLAYER_JS = r"""
<script>
function showAll(b){var s=b.dataset.sec;document.querySelectorAll('.'+s+'-x').forEach(function(e){e.style.display='';});b.style.display='none';}
function og(){document.getElementById('gmodal').classList.add('open');}
function cg(e){if(!e||e.target.id==='gmodal'||e.target.classList.contains('gclose'))document.getElementById('gmodal').classList.remove('open');}
</script>
"""


def player_page(pid, name, flag, data, similar, glossary_html):
    first, last = name
    full = f"{first} {last}"
    streaks = data["streaks"]
    lens = {t: streaks[t]["len"] for t in TYPES}
    priority = {"combined": 3, "regular": 2, "playoff": 1}
    best = max(lens, key=lambda t: (lens[t], priority[t]))
    seo_word, back_name, back_href = TYPE_META[best]
    title = f"{full}: {lens[best]} Consecutive {seo_word} Games | NBA Iron Man Streaks"

    badges = "".join(
        f'<span class="badge {"on" if lens[t] > 0 else "off"}">{BADGE_TEXT[t]}: '
        f'<b>{lens[t]}</b> consecutive games</span>' for t in TYPES
    )

    legend = ('<div class="legend"><span><i class="sq g"></i> Appeared</span>'
              '<span><i class="sq r"></i> Missed</span>'
              '<span><i class="sq d"></i> DNP (coach/inactive)</span></div>')
    trows = []
    for row in data["timeline"]:
        sq = "".join(f'<i class="sq {c}" title="{esc(t)}"></i>' for c, t in row["squares"])
        trows.append(
            f'<div class="trow"><div class="tlabel"><span class="tseason">{esc(row["label"])}</span>'
            f'<span class="tteam">{esc(row["team"])}</span></div>'
            f'<div class="tsquares">{sq}</div></div>'
        )
    timeline_html = (legend + '<div class="tlcard">' + "".join(trows) + '</div>'
                     if trows else '<p class="subtitle">No games on record.</p>')

    # #3 iron man streaks (no Type column)
    if data["iron"]:
        cells = [
            (f'<td class="num">{s["games"]}</td><td class="num">{s["start"].isoformat()}</td>'
             f'<td class="num">{s["end"].isoformat()}</td><td>{esc(s["team"])}</td>')
            for s in data["iron"]
        ]
        iron_html = table_showall("iron", ["Games", "Start", "End", "Team"], cells)
    else:
        iron_html = f'<p class="subtitle">No streaks of {MIN_IRON}+ games.</p>'

    # missed seasons
    if data["missed"]:
        mrows = "".join(
            f'<tr><td>{esc(m["label"])}</td><td>{esc(m["team"])}</td>'
            f'<td class="num">{m["games"]}</td>'
            f'<td><a class="plink" href="../reasons/{m["rslug"]}.html">{esc(m["reason"])}</a></td></tr>'
            for m in data["missed"]
        )
        missed_html = (
            '<h3>Missed Seasons (injury/illness)</h3><div class="table-card">'
            '<table class="dtable"><thead><tr><th>Season</th>'
            '<th>Team</th><th>Games Missed</th><th>Reason</th></tr></thead>'
            f'<tbody>{mrows}</tbody></table></div>'
        )
    else:
        missed_html = ""

    # #4 absences (no Type column), reason links to reason page
    if data["absences"]:
        cells = [
            (f'<td class="num">{a["count"]}</td><td class="num">{a["days"]}</td>'
             f'<td class="num">{a["frm"].isoformat()}</td><td class="num">{a["to"].isoformat()}</td>'
             f'<td><a class="plink" href="../reasons/{a["rslug"]}.html">{esc(a["reason"])}</a></td>')
            for a in data["absences"]
        ]
        abs_html = table_showall("abs", ["Games Missed", "Days", "From", "To", "Reason"], cells)
    else:
        abs_html = '<p class="subtitle">No absences — never missed a game their team played.</p>'

    sim_cards = "".join(
        f'<a class="simcard" href="{esc(s["slug"])}.html">'
        f'<div class="nm">{esc(s["name"])}{flag_html(s["flag"])}</div>'
        f'<div class="st">{s["top"]} game streak</div></a>'
        for s in similar
    )

    back = f'<a class="backtop" href="{back_href}">← Back to {back_name} Leaderboard</a>'
    body = (
        f"{nav('../', None)}\n<div class=\"wrap\">\n{back}\n"
        f"<header><h1>{name_with_flag(full, flag)}</h1><div class=\"badges\">{badges}</div></header>\n"
        f"<section><h2>Season Timeline</h2>{timeline_html}</section>\n"
        f"<section><h2>Iron Man Streaks</h2>{iron_html}</section>\n"
        f'<section><h2>Absences <button class="gbtn" title="What do these codes mean?" onclick="og()">ⓘ</button></h2>'
        f"{missed_html}{abs_html}</section>\n"
        f'<section><h2>Similar Players</h2><div class="simgrid">{sim_cards}</div></section>\n'
        f'<a class="backtop" href="{back_href}">← Back to {back_name} Leaderboard</a>\n</div>\n'
        f'<div class="modal" id="gmodal" onclick="cg(event)"><div class="modal-card">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">'
        f'<h3 style="margin:0;">Absence reason glossary</h3>'
        f'<button class="gbtn gclose" onclick="cg()">✕</button></div>{glossary_html}</div></div>'
    )
    return page(title, body, PLAYER_JS)


# --------------------------------------------------------------------------- #
# Reasons index + per-reason pages
# --------------------------------------------------------------------------- #
def reasons_index_page(reason_total, reason_detail, names, summaries):
    blocks = []
    for reason, total in sorted(reason_total.items(), key=lambda kv: (-kv[1], kv[0])):
        slug = reason_slug(reason)
        top = sorted(reason_detail[reason].items(), key=lambda kv: -kv[1][0])[:10]
        rows = "".join(
            f'<tr><td><a class="plink" href="players/{summaries[p]["slug"]}.html">'
            f'{esc(summaries[p]["name"])}</a></td><td class="num">{cell[0]}</td></tr>'
            for p, cell in top
        )
        blocks.append(
            f'<div class="reasonblock"><h2><a href="reasons/{slug}.html">{esc(reason)}</a> '
            f'<span class="rcount">{total} games missed across all players</span></h2>'
            f'<table class="dtable"><thead><tr><th>Most affected player</th>'
            f'<th>Games</th></tr></thead><tbody>{rows}</tbody></table></div>'
        )
    body = (
        f"{nav('', 'reasons')}\n<div class=\"wrap\">\n"
        "<header><h1>Absence <span class=\"accent\">Reasons</span></h1>"
        "<p class=\"subtitle\">Every normalized reason in the data — how Iron Man streaks end. Click a reason for its own page.</p></header>\n"
        + "".join(blocks) + "\n</div>"
    )
    return page("Absence Reasons | NBA Iron Man Streaks", body)


REASON_JS = r"""
<script>
const D=__DATA__;
const NUM=new Set([1]);
let sk=1, sa=false;
const tb=document.getElementById('tbody'),hs=Array.from(document.querySelectorAll('thead th'));
function eh(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function cmp(a,b,k){let av,bv;if(k===2){av=a[6];bv=b[6];}else{av=a[k];bv=b[k];}if(NUM.has(k)){av=+av;bv=+bv;}else{av=String(av).toLowerCase();bv=String(bv).toLowerCase();}return av<bv?-1:av>bv?1:0;}
function rnd(){let rows=D.slice().sort((a,b)=>{const c=cmp(a,b,sk);return sa?c:-c;});
 tb.innerHTML=rows.map(r=>'<tr><td><a class="plink" href="../players/'+r[4]+'.html">'+eh(r[0])+'</a>'+(r[5]?' <img src="https://flagcdn.com/24x18/'+r[5][0]+'.png" width="24" height="18" alt="'+eh(r[5][1])+'" class="flag">':'')+'</td><td class="num">'+r[1]+'</td><td class="num">'+eh(r[2])+'</td><td>'+eh(r[3])+'</td></tr>').join('');
 hs.forEach(h=>{const k=+h.dataset.key;h.classList.toggle('active',k===sk);const e=h.querySelector('.arrow');if(e)e.remove();if(k===sk){const s=document.createElement('span');s.className='arrow';s.textContent=sa?'▲':'▼';h.appendChild(s);}});}
hs.forEach(h=>h.addEventListener('click',()=>{const k=+h.dataset.key;if(k===sk)sa=!sa;else{sk=k;sa=!(NUM.has(k)||k===2);}rnd();}));
rnd();
</script>
"""


def reason_page(reason, total, detail, names, summaries):
    rows = []
    for pid, cell in detail.items():
        cnt, mn, mx, teams = cell
        sm = summaries[pid]
        rng = mn.isoformat() if mn == mx else f"{mn.isoformat()} → {mx.isoformat()}"
        team = teams.most_common(1)[0][0] if teams else ""
        rows.append([sm["name"], cnt, rng, team, sm["slug"], sm["flag"], mn.isoformat()])
    rows.sort(key=lambda r: -r[1])
    body = (
        f"{nav('../', 'reasons')}\n<div class=\"wrap\">\n"
        f'<a class="backtop" href="../reasons.html">← Back to all Absence Reasons</a>\n'
        f"<header><h1>{esc(reason)}</h1>"
        f'<p class="subtitle">{total} games missed across {len(detail)} players with this reason.</p></header>\n'
        '<div class="table-card" style="padding:0;"><table class="dtable" style="margin:0;"><thead><tr>'
        '<th data-key="0">Player</th><th data-key="1">Games Missed</th>'
        '<th data-key="2">Date Range</th><th data-key="3">Team</th>'
        '</tr></thead><tbody id="tbody"></tbody></table></div>\n</div>'
    )
    scripts = REASON_JS.replace("__DATA__", json.dumps(rows, separators=(",", ":"), ensure_ascii=False))
    return page(f"{reason} Absences | NBA Iron Man Streaks", body, scripts)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    force = "--force" in sys.argv
    os.makedirs(PLAYERS_DIR, exist_ok=True)
    os.makedirs(REASONS_DIR, exist_ok=True)

    game_info, ts_pf, ts_reg, ts_comb = load_games()
    games, names, appeared = load_players(game_info)
    country_by_pid, birth_year, meta_ok = load_players_meta()
    nationalities, nat_ok = load_nationalities()
    flags, overridden = build_flags(names, country_by_pid, nationalities)
    print(f"Flags: {len(flags)} players | nationality overrides: {overridden} "
          f"(nationalities.csv {'loaded' if nat_ok else 'MISSING'})")

    players = sorted(pid for pid in games if appeared.get(pid))
    total = len(players)

    tracked_seasons = set()
    for gs in appeared.values():
        for gid in gs:
            tracked_seasons.add(nba_season(game_info[gid][0]))

    # ---- pass 1: streaks, summaries, leaderboards, reason + absence aggregates #
    print(f"\nPass 1: computing streaks for {total} players...")
    boards = {t: [] for t in TYPES}
    summaries = {}
    reason_total = Counter()
    reason_detail = defaultdict(lambda: defaultdict(lambda: [0, None, None, Counter()]))
    global_absences = []
    corpus_lower = set()

    for pid in players:
        res = analyze(pid, False, games, appeared, game_info, ts_pf, ts_reg, ts_comb)
        st = res["streaks"]
        lens = {t: st[t]["len"] for t in TYPES}
        first, last = names[pid]
        slug = slugify(first, last, pid)
        flag = flags.get(pid, "")
        for t in TYPES:
            s = st[t]
            if s["len"] > 0:
                boards[t].append({
                    "first": first, "last": last, "name": f"{first} {last}",
                    "slug": slug, "flag": flag, "len": s["len"],
                    "start": s["start"].isoformat() if s["start"] else "",
                    "end": s["end"].isoformat() if s["end"] else "",
                })
        summaries[pid] = {"name": f"{first} {last}", "slug": slug, "flag": flag,
                          "top": max(lens.values()), "clen": lens["combined"]}
        for a in res["absences"]:
            global_absences.append({**a, "pid": pid})
        for gid, row in games[pid].items():
            if row[1] == 0 and row[2]:
                corpus_lower.add(row[2].lower())
                r = normalize_reason(row[2])
                if r and r != "—":
                    reason_total[r] += 1
                    d = game_info[gid][0]
                    cell = reason_detail[r][pid]
                    cell[0] += 1
                    if cell[1] is None or d < cell[1]:
                        cell[1] = d
                    if cell[2] is None or d > cell[2]:
                        cell[2] = d
                    c, n = team_city_name(game_info, gid, row[0])
                    cell[3][f"{c} {n}".strip()] += 1

    glossary_html = build_glossary_html(corpus_lower)
    global_absences.sort(key=lambda a: a["count"], reverse=True)
    boards_sorted = {t: sorted(boards[t], key=lambda r: (-r["len"], r["last"].lower(), r["first"].lower()))
                     for t in TYPES}

    # similar players: by closest birth year, fall back to combined length
    by_players = sorted((p for p in players if birth_year.get(p)), key=lambda p: birth_year[p])
    by_pos = {p: i for i, p in enumerate(by_players)}
    clen_sorted = sorted(players, key=lambda p: summaries[p]["clen"])
    clen_pos = {p: i for i, p in enumerate(clen_sorted)}

    def neighbors(seq, posmap, pid, keyfn):
        i = posmap[pid]
        n = len(seq)
        lo, hi = i - 1, i + 1
        picks = []
        base = keyfn(pid)
        while len(picks) < 6 and (lo >= 0 or hi < n):
            cand = []
            if lo >= 0:
                cand.append((abs(keyfn(seq[lo]) - base), lo, "lo"))
            if hi < n:
                cand.append((abs(keyfn(seq[hi]) - base), hi, "hi"))
            cand.sort()
            _, idx, side = cand[0]
            picks.append(summaries[seq[idx]])
            if side == "lo":
                lo -= 1
            else:
                hi += 1
        return picks

    def similar_for(pid):
        if birth_year.get(pid) and pid in by_pos:
            return neighbors(by_players, by_pos, pid, lambda p: birth_year[p])
        return neighbors(clen_sorted, clen_pos, pid, lambda p: summaries[p]["clen"])

    # ---- pass 2: player pages --------------------------------------------- #
    print("Pass 2: writing player pages...\n")
    generated = 0
    for i, pid in enumerate(players, start=1):
        out_path = os.path.join(PLAYERS_DIR, f"{summaries[pid]['slug']}.html")
        if force or not os.path.exists(out_path):
            res = analyze(pid, True, games, appeared, game_info, ts_pf, ts_reg, ts_comb,
                          tracked_seasons)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(player_page(pid, names[pid], flags.get(pid, ""), res,
                                    similar_for(pid), glossary_html))
            generated += 1
        if i % 500 == 0:
            print(f"Generating player pages... {i}/{total}")

    # ---- leaderboards (#5: four sections each) ----------------------------- #
    titles = {
        "regular": ('NBA Regular Season <span class="accent">Iron Man</span> Streaks',
                    os.path.join(BASE_DIR, "regular.html")),
        "playoff": ('NBA Playoff <span class="accent">Iron Man</span> Streaks',
                    os.path.join(BASE_DIR, "index.html")),
        "combined": ('NBA <span class="accent">Iron Man</span> Streaks (Regular Season + Playoffs)',
                     os.path.join(BASE_DIR, "combined.html")),
    }
    for t, (title_html, path) in titles.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(leaderboard_page(title_html, boards_sorted[t], global_absences, summaries, t))
        print(f"Wrote {os.path.basename(path)} ({len(boards_sorted[t])} players).")

    # ---- reasons index + per-reason pages ---------------------------------- #
    with open(os.path.join(BASE_DIR, "reasons.html"), "w", encoding="utf-8") as f:
        f.write(reasons_index_page(reason_total, reason_detail, names, summaries))
    reason_pages = 0
    for reason, total_g in reason_total.items():
        slug = reason_slug(reason)
        with open(os.path.join(REASONS_DIR, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(reason_page(reason, total_g, reason_detail[reason], names, summaries))
        reason_pages += 1
    print(f"Wrote reasons.html + {reason_pages} reason pages.")

    note = "" if nat_ok else " | NOTE: nationalities.csv missing — flags use Players.csv country only"
    print(f"\nDone. 3 leaderboards + reasons index + {reason_pages} reason pages + "
          f"{generated} player pages.{note}")


if __name__ == "__main__":
    main()
