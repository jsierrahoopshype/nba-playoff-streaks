"""
streak_analysis.py

NBA "Iron Man" streaks: the longest run of consecutive games a player APPEARED
IN, measured across their ENTIRE CAREER, spanning team changes.

This computes THREE flavors of the same metric:
  * Playoffs        (gameType == "Playoffs")        -> playoff_streaks.csv
  * Regular season  (gameType == "Regular Season")  -> regular_streaks.csv
  * Combined        (both of the above)             -> combined_streaks.csv

Definition (identical career-wide logic for all three)
------------------------------------------------------
"Appeared" = the player has a row for the game AND numMinutes > 0.

For every game in the relevant universe, relative to a given player:
  * The player's team played AND the player appeared  -> streak + 1
  * The player's team played AND the player did NOT appear (DNP / missing row)
        -> streak resets to 0
  * The player's team did NOT play (team out of contention, or the player was
        between teams) -> NEUTRAL: the game is skipped, no effect.

The streak therefore carries across team changes and across seasons where the
player's team simply wasn't playing in that universe.

"The player's team played" is determined per (season, team): a player
participated for a team in a season iff they have at least one row (appeared OR
DNP) for that team that season. For every such (season, team), ALL of that
team's games in that universe/season enter the player's career sequence;
appearance vs. non-appearance then drives increment vs. reset. Every other game
is neutral.

Season key
----------
  * Playoffs: calendar year of the game date (a postseason lies within one
    calendar year), as in the original playoff logic.
  * Regular season / Combined: NBA-season-start year (games from Jul onward map
    to that year; Jan-Jun map to the prior year). This keeps Oct->Apr together,
    and for Combined bundles a regular season with the playoffs that follow it.
    The offseason between consecutive seasons is then a neutral gap.

Outputs
-------
  - playoff_streaks.csv:  firstName, lastName, teamCity, teamName, streakLength,
                          streakStartDate, streakEndDate
  - regular_streaks.csv / combined_streaks.csv:
                          firstName, lastName, personId, teamCity, teamName,
                          streakLength, streakStartDate, streakEndDate
    (team = the team of the streak's peak/last game)
"""

import os
from collections import defaultdict

import pandas as pd

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #
BASE_DIR = r"C:\nba-streaks"
DATA_DIR = os.path.join(BASE_DIR, "data")
GAMES_CSV = os.path.join(DATA_DIR, "Games.csv")
PLAYERSTATS_CSV = os.path.join(DATA_DIR, "PlayerStatistics.csv")
OUTPUT_CSV = os.path.join(BASE_DIR, "playoff_streaks.csv")
REGULAR_CSV = os.path.join(BASE_DIR, "regular_streaks.csv")
COMBINED_CSV = os.path.join(BASE_DIR, "combined_streaks.csv")

PLAYOFF_LABEL = "Playoffs"
REGULAR_LABEL = "Regular Season"
ONE_GB = 1_000_000_000


# --------------------------------------------------------------------------- #
# Games.csv -> per-(team, season) playoff schedule
# --------------------------------------------------------------------------- #
def load_games():
    """Return:
        teamseason: dict[(teamId, season)] -> list[(gameDate, gameId)]
        gid_season: dict[gameId] -> season (year)
    covering playoff games only.
    """
    games = pd.read_csv(
        GAMES_CSV,
        usecols=["gameId", "hometeamId", "awayteamId", "gameType", "gameDate"],
        low_memory=False,
    )

    print("Unique gameType values in Games.csv:")
    for val, cnt in games["gameType"].value_counts(dropna=False).items():
        marker = "  <-- PLAYOFF FILTER" if val == PLAYOFF_LABEL else ""
        print(f"  {val!r}: {cnt}{marker}")
    print()

    games = games[games["gameType"] == PLAYOFF_LABEL].copy()
    games["gameId"] = games["gameId"].astype(str)
    games["gameDate"] = pd.to_datetime(games["gameDate"], errors="coerce")
    games = games.dropna(subset=["gameDate", "hometeamId", "awayteamId"])
    games["season"] = games["gameDate"].dt.year.astype("int64")
    games["hometeamId"] = games["hometeamId"].astype("int64")
    games["awayteamId"] = games["awayteamId"].astype("int64")

    teamseason = defaultdict(list)
    gid_season = {}
    for gid, date, season, home, away in zip(
        games["gameId"], games["gameDate"], games["season"],
        games["hometeamId"], games["awayteamId"],
    ):
        teamseason[(home, season)].append((date, gid))
        teamseason[(away, season)].append((date, gid))
        gid_season[gid] = season

    print(f"Playoff games loaded: {len(gid_season)} across "
          f"{games['season'].nunique()} postseasons.\n")
    return teamseason, gid_season


# --------------------------------------------------------------------------- #
# PlayerStatistics.csv -> playoff rows
# --------------------------------------------------------------------------- #
def load_player_playoffs(gid_season):
    cols = [
        "firstName", "lastName", "personId", "gameId",
        "playerteamCity", "playerteamName", "playerteamId",
        "gameType", "numMinutes",
    ]

    size = os.path.getsize(PLAYERSTATS_CSV)
    if size > ONE_GB:
        print(f"PlayerStatistics.csv is {size/1e9:.2f} GB -> loading in chunks.\n")
        parts = []
        for chunk in pd.read_csv(
            PLAYERSTATS_CSV, usecols=cols, chunksize=500_000, low_memory=False
        ):
            parts.append(chunk[chunk["gameType"] == PLAYOFF_LABEL])
        ps = pd.concat(parts, ignore_index=True)
    else:
        print(f"PlayerStatistics.csv is {size/1e9:.2f} GB -> loading in one pass.\n")
        ps = pd.read_csv(PLAYERSTATS_CSV, usecols=cols, low_memory=False)
        ps = ps[ps["gameType"] == PLAYOFF_LABEL].copy()

    ps["gameId"] = ps["gameId"].astype(str)
    ps = ps.dropna(subset=["playerteamId"])
    ps["playerteamId"] = ps["playerteamId"].astype("int64")
    ps["numMinutes"] = pd.to_numeric(ps["numMinutes"], errors="coerce").fillna(0.0)

    # Keep only rows for games that exist in the playoff games universe.
    ps["season"] = ps["gameId"].map(gid_season)
    ps = ps.dropna(subset=["season"])
    ps["season"] = ps["season"].astype("int64")

    ps["appeared"] = ps["numMinutes"] > 0
    return ps


# --------------------------------------------------------------------------- #
# Streak computation
# --------------------------------------------------------------------------- #
def compute_streaks(teamseason, ps):
    # Per-player aggregates.
    appeared_ids = defaultdict(set)        # personId -> set(gameId) appeared
    participations = defaultdict(set)      # personId -> set((season, teamId))
    names = {}                             # personId -> (firstName, lastName)
    # Team identity for the peak game (only appeared games can be a peak).
    team_of_appeared = {}                  # (personId, gameId) -> (city, name)

    for row in ps.itertuples(index=False):
        pid = row.personId
        participations[pid].add((row.season, row.playerteamId))
        if pid not in names:
            names[pid] = (row.firstName, row.lastName)
        if row.appeared:
            appeared_ids[pid].add(row.gameId)
            team_of_appeared[(pid, row.gameId)] = (row.playerteamCity, row.playerteamName)

    results = []
    for pid, parts in participations.items():
        # Build the player's career playoff-game sequence: every game played by
        # every team they were rostered on, in the seasons they were rostered.
        seq = {}  # gameId -> gameDate (dedup; a game belongs to one team/season)
        for season, team_id in parts:
            for date, gid in teamseason.get((team_id, season), ()):
                seq[gid] = date
        if not seq:
            continue

        ordered = sorted(seq.items(), key=lambda kv: (kv[1], kv[0]))
        appeared = appeared_ids.get(pid, set())

        max_len, max_start, max_end, max_end_gid = 0, None, None, None
        cur, cur_start = 0, None
        for gid, date in ordered:
            if gid in appeared:
                if cur == 0:
                    cur_start = date
                cur += 1
                if cur > max_len:
                    max_len, max_start, max_end, max_end_gid = cur, cur_start, date, gid
            else:
                cur = 0

        if max_len == 0:
            continue

        first, last = names[pid]
        city, name = team_of_appeared.get((pid, max_end_gid), ("", ""))
        results.append({
            "firstName": first,
            "lastName": last,
            "teamCity": city,
            "teamName": name,
            "streakLength": max_len,
            "streakStartDate": max_start,
            "streakEndDate": max_end,
        })

    return pd.DataFrame(results)


# =========================================================================== #
# Regular-season / combined additions
# (Same career-wide logic as the playoff code above; only the gameType filter
#  and the season key differ. The playoff functions above are untouched.)
# =========================================================================== #
def nba_season_key(date_series):
    """NBA season-start year: games in Jul-Dec belong to that calendar year's
    season; Jan-Jun belong to the prior year. Keeps Oct->Apr (and the playoffs
    that follow, Apr-Jun) under a single season key."""
    y = date_series.dt.year
    m = date_series.dt.month
    return y.where(m >= 7, y - 1).astype("int64")


def load_games_generic(gametypes, season_func, label):
    """Like load_games(), but for an arbitrary set of gameType values and an
    arbitrary season-key function. Returns (teamseason, gid_season)."""
    games = pd.read_csv(
        GAMES_CSV,
        usecols=["gameId", "hometeamId", "awayteamId", "gameType", "gameDate"],
        low_memory=False,
    )
    games = games[games["gameType"].isin(gametypes)].copy()
    games["gameId"] = games["gameId"].astype(str)
    games["gameDate"] = pd.to_datetime(games["gameDate"], errors="coerce")
    games = games.dropna(subset=["gameDate", "hometeamId", "awayteamId"])
    games["season"] = season_func(games["gameDate"])
    games["hometeamId"] = games["hometeamId"].astype("int64")
    games["awayteamId"] = games["awayteamId"].astype("int64")

    teamseason = defaultdict(list)
    gid_season = {}
    for gid, date, season, home, away in zip(
        games["gameId"], games["gameDate"], games["season"],
        games["hometeamId"], games["awayteamId"],
    ):
        teamseason[(home, season)].append((date, gid))
        teamseason[(away, season)].append((date, gid))
        gid_season[gid] = season

    print(f"[{label}] games loaded: {len(gid_season)} across "
          f"{games['season'].nunique()} seasons.\n")
    return teamseason, gid_season


def load_player_generic(gid_season, gametypes, label):
    """Like load_player_playoffs(), but for an arbitrary set of gameType values
    and an externally supplied season map (gid_season)."""
    cols = [
        "firstName", "lastName", "personId", "gameId",
        "playerteamCity", "playerteamName", "playerteamId",
        "gameType", "numMinutes",
    ]

    size = os.path.getsize(PLAYERSTATS_CSV)
    if size > ONE_GB:
        print(f"[{label}] PlayerStatistics.csv is {size/1e9:.2f} GB -> chunked load.\n")
        parts = []
        for chunk in pd.read_csv(
            PLAYERSTATS_CSV, usecols=cols, chunksize=500_000, low_memory=False
        ):
            parts.append(chunk[chunk["gameType"].isin(gametypes)])
        ps = pd.concat(parts, ignore_index=True)
    else:
        print(f"[{label}] PlayerStatistics.csv is {size/1e9:.2f} GB -> single pass.\n")
        ps = pd.read_csv(PLAYERSTATS_CSV, usecols=cols, low_memory=False)
        ps = ps[ps["gameType"].isin(gametypes)].copy()

    ps["gameId"] = ps["gameId"].astype(str)
    ps = ps.dropna(subset=["playerteamId"])
    ps["playerteamId"] = ps["playerteamId"].astype("int64")
    ps["numMinutes"] = pd.to_numeric(ps["numMinutes"], errors="coerce").fillna(0.0)

    ps["season"] = ps["gameId"].map(gid_season)
    ps = ps.dropna(subset=["season"])
    ps["season"] = ps["season"].astype("int64")

    ps["appeared"] = ps["numMinutes"] > 0
    return ps


def compute_streaks_generic(teamseason, ps):
    """Carries personId through to the result, AND bounds each (season, team)
    stint to the player's actual tenure window for that team that season.

    Tenure window = [earliest, latest] date among the player's rows (appeared OR
    DNP) for that (season, team). Only that team's games inside the window enter
    the player's career sequence. This prevents a mid-season trade from
    interleaving the new team's pre-arrival games and the old team's post-trade
    games as spurious streak breaks. For a single-team season the window spans
    the whole season, so behavior is unchanged; for playoffs (handled by the
    untouched compute_streaks) a stint already covers the full postseason, so
    this windowing would be a no-op there too."""
    # Date for every game in the universe (a gameId maps to a single date).
    gid_date = {}
    for games_list in teamseason.values():
        for date, gid in games_list:
            gid_date[gid] = date

    appeared_ids = defaultdict(set)
    participations = defaultdict(set)
    names = {}
    team_of_appeared = {}
    win = {}  # (personId, season, teamId) -> [min_date, max_date] over the player's rows

    for row in ps.itertuples(index=False):
        pid = row.personId
        participations[pid].add((row.season, row.playerteamId))

        d = gid_date.get(row.gameId)
        if d is not None:
            key = (pid, row.season, row.playerteamId)
            cur = win.get(key)
            if cur is None:
                win[key] = [d, d]
            else:
                if d < cur[0]:
                    cur[0] = d
                if d > cur[1]:
                    cur[1] = d

        if pid not in names:
            names[pid] = (row.firstName, row.lastName)
        if row.appeared:
            appeared_ids[pid].add(row.gameId)
            team_of_appeared[(pid, row.gameId)] = (row.playerteamCity, row.playerteamName)

    results = []
    for pid, parts in participations.items():
        seq = {}
        for season, team_id in parts:
            window = win.get((pid, season, team_id))
            if window is None:
                continue
            wmin, wmax = window
            for date, gid in teamseason.get((team_id, season), ()):
                if wmin <= date <= wmax:
                    seq[gid] = date
        if not seq:
            continue

        ordered = sorted(seq.items(), key=lambda kv: (kv[1], kv[0]))
        appeared = appeared_ids.get(pid, set())

        max_len, max_start, max_end, max_end_gid = 0, None, None, None
        cur, cur_start = 0, None
        for gid, date in ordered:
            if gid in appeared:
                if cur == 0:
                    cur_start = date
                cur += 1
                if cur > max_len:
                    max_len, max_start, max_end, max_end_gid = cur, cur_start, date, gid
            else:
                cur = 0

        if max_len == 0:
            continue

        first, last = names[pid]
        city, name = team_of_appeared.get((pid, max_end_gid), ("", ""))
        results.append({
            "firstName": first,
            "lastName": last,
            "personId": pid,
            "teamCity": city,
            "teamName": name,
            "streakLength": max_len,
            "streakStartDate": max_start,
            "streakEndDate": max_end,
        })

    return pd.DataFrame(results)


def write_streaks_with_id(result, out_path, label, top_n):
    """Sort, write CSV (with personId column), and print the top N."""
    if result.empty:
        print(f"No {label} streaks found.\n")
        return

    result = result.sort_values(
        ["streakLength", "lastName", "firstName"], ascending=[False, True, True]
    ).reset_index(drop=True)

    out = result.copy()
    out["personId"] = out["personId"].astype("int64")
    out["streakStartDate"] = out["streakStartDate"].dt.strftime("%Y-%m-%d")
    out["streakEndDate"] = out["streakEndDate"].dt.strftime("%Y-%m-%d")
    out = out[[
        "firstName", "lastName", "personId", "teamCity", "teamName",
        "streakLength", "streakStartDate", "streakEndDate",
    ]]
    out.to_csv(out_path, index=False)
    print(f"Wrote {len(out)} players to {out_path}\n")

    print(f"Top {top_n} {label} consecutive-game appearance streaks (career, all teams):")
    print("-" * 92)
    print(f"{'#':>3}  {'Player':<26}{'Peak Team':<26}{'Len':>4}  {'Start':<11}{'End':<11}")
    print("-" * 92)
    for i, row in out.head(top_n).iterrows():
        player = f"{row['firstName']} {row['lastName']}"
        team = f"{row['teamCity']} {row['teamName']}"
        print(
            f"{i+1:>3}  {player:<26}{team:<26}{row['streakLength']:>4}  "
            f"{row['streakStartDate']:<11}{row['streakEndDate']:<11}"
        )
    print("-" * 92)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    # ---- Playoffs (unchanged) ---------------------------------------------- #
    teamseason, gid_season = load_games()
    ps = load_player_playoffs(gid_season)
    result = compute_streaks(teamseason, ps)

    if result.empty:
        print("No playoff streaks found — check the data/filters.")
        return

    result = result.sort_values(
        ["streakLength", "lastName", "firstName"], ascending=[False, True, True]
    ).reset_index(drop=True)

    out = result.copy()
    out["streakStartDate"] = out["streakStartDate"].dt.strftime("%Y-%m-%d")
    out["streakEndDate"] = out["streakEndDate"].dt.strftime("%Y-%m-%d")
    out = out[[
        "firstName", "lastName", "teamCity", "teamName",
        "streakLength", "streakStartDate", "streakEndDate",
    ]]
    out.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote {len(out)} players to {OUTPUT_CSV}\n")

    print("Top 25 consecutive-playoff-game appearance streaks (career, all teams):")
    print("-" * 92)
    print(f"{'#':>3}  {'Player':<26}{'Peak Team':<26}{'Len':>4}  {'Start':<11}{'End':<11}")
    print("-" * 92)
    for i, row in out.head(25).iterrows():
        player = f"{row['firstName']} {row['lastName']}"
        team = f"{row['teamCity']} {row['teamName']}"
        print(
            f"{i+1:>3}  {player:<26}{team:<26}{row['streakLength']:>4}  "
            f"{row['streakStartDate']:<11}{row['streakEndDate']:<11}"
        )
    print("-" * 92)

    # ---- Regular season & combined ----------------------------------------- #
    print("\n" + "=" * 92)
    print("REGULAR SEASON & COMBINED")
    print("=" * 92 + "\n")

    ts_reg, _ = load_games_generic({REGULAR_LABEL}, nba_season_key, "Regular Season")
    ts_comb, gid_comb = load_games_generic(
        {REGULAR_LABEL, PLAYOFF_LABEL}, nba_season_key, "Combined"
    )

    # Load the regular+playoff player rows once; derive the regular-only subset.
    ps_comb = load_player_generic(gid_comb, {REGULAR_LABEL, PLAYOFF_LABEL}, "Combined")
    ps_reg = ps_comb[ps_comb["gameType"] == REGULAR_LABEL].copy()

    reg_result = compute_streaks_generic(ts_reg, ps_reg)
    comb_result = compute_streaks_generic(ts_comb, ps_comb)

    write_streaks_with_id(reg_result, REGULAR_CSV, "REGULAR SEASON", 10)
    print()
    write_streaks_with_id(comb_result, COMBINED_CSV, "COMBINED (REG + PLAYOFFS)", 10)


if __name__ == "__main__":
    main()
