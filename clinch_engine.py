import datetime
import json
import os
import re
import requests
from bs4 import BeautifulSoup

TOTAL_GAMES = 143
GAMES_PER_OPPONENT_INTRA = 25  # 同一リーグ対戦数
GAMES_PER_OPPONENT_INTER = 3   # 交流戦対戦数
DB_FILE = "games_db.json"
HISTORY_FILE = "history_standings.json"

TEAM_MAP = {
    "DeNA": "ＤｅＮＡ", "横浜": "ＤｅＮＡ", "ソフトバンク": "ソフトバンク",
    "ロッテ": "ロッテ", "楽天": "楽天", "オリックス": "オリックス",
    "日本ハム": "日本ハム", "西武": "西武", "阪神": "阪神",
    "巨人": "巨人", "広島": "広島", "ヤクルト": "ヤクルト", "中日": "中日"
}

CENTRAL_TEAMS = ["阪神", "巨人", "ＤｅＮＡ", "ヤクルト", "中日", "広島"]
PACIFIC_TEAMS = ["ソフトバンク", "日本ハム", "ロッテ", "楽天", "オリックス", "西武"]

# 直近ベースライン（2026-09-14終了時点の確定成績）
BASELINE_RECORDS = {
    "阪神": {"games": 127, "win": 69, "lose": 57, "draw": 1},
    "巨人": {"games": 131, "win": 70, "lose": 59, "draw": 2},
    "ＤｅＮＡ": {"games": 130, "win": 64, "lose": 63, "draw": 3},
    "ヤクルト": {"games": 129, "win": 56, "lose": 71, "draw": 2},
    "中日": {"games": 133, "win": 57, "lose": 74, "draw": 2},
    "広島": {"games": 126, "win": 52, "lose": 70, "draw": 4},
    "ソフトバンク": {"games": 127, "win": 79, "lose": 45, "draw": 3},
    "日本ハム": {"games": 129, "win": 68, "lose": 53, "draw": 8},
    "ロッテ": {"games": 126, "win": 63, "lose": 57, "draw": 6},
    "楽天": {"games": 125, "win": 60, "lose": 62, "draw": 3},
    "オリックス": {"games": 128, "win": 57, "lose": 68, "draw": 3},
    "西武": {"games": 129, "win": 43, "lose": 84, "draw": 2}
}

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "season": 2026,
        "teams": {"central": CENTRAL_TEAMS, "pacific": PACIFIC_TEAMS},
        "games": []
    }

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def calc_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def build_standings_at_date(db, target_date_str):
    """指定日時点の勝敗表および直接対決消化数を計算"""
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS
    records = {t: dict(BASELINE_RECORDS[t]) for t in all_teams}
    for t in all_teams:
        records[t]["team"] = t

    h2h_played = {t1: {t2: 20 for t2 in all_teams} for t1 in all_teams}

    # 2026-09-14以降の新規終了試合を加算
    for g in db.get("games", []):
        if g.get("status") == "finished" and "2026-09-14" < g.get("date") <= target_date_str:
            h, a = g["home"], g["away"]
            hs, as_ = g["home_score"], g["away_score"]
            if h not in records or a not in records:
                continue

            records[h]["games"] += 1
            records[a]["games"] += 1
            h2h_played[h][a] += 1
            h2h_played[a][h] += 1

            if hs > as_:
                records[h]["win"] += 1
                records[a]["lose"] += 1
            elif hs < as_:
                records[a]["win"] += 1
                records[h]["lose"] += 1
            else:
                records[h]["draw"] += 1
                records[a]["draw"] += 1

    def format_league(team_list):
        res = []
        for t in team_list:
            r = records[t]
            r["rate"] = calc_rate(r["win"], r["lose"])
            res.append(r)
        res.sort(key=lambda x: (x["rate"], x["win"]), reverse=True)
        top_w, top_l = res[0]["win"], res[0]["lose"]
        for idx, t in enumerate(res):
            t["rank"] = idx + 1
            diff = ((top_w - t["win"]) + (t["lose"] - top_l)) / 2.0
            t["diff"] = max(0.0, diff) if idx > 0 else 0.0
        return res

    return {
        "central": format_league(CENTRAL_TEAMS),
        "pacific": format_league(PACIFIC_TEAMS)
    }, h2h_played

def calc_clinch_magic_h2h(team_a, border_team, h2h_played):
    ta, tb = team_a["team"], border_team["team"]
    rem_a = TOTAL_GAMES - team_a["games"]
    rem_b = TOTAL_GAMES - border_team["games"]

    is_same = (ta in CENTRAL_TEAMS and tb in CENTRAL_TEAMS) or (ta in PACIFIC_TEAMS and tb in PACIFIC_TEAMS)
    max_h2h = GAMES_PER_OPPONENT_INTRA if is_same else GAMES_PER_OPPONENT_INTER
    played_h2h = h2h_played[ta][tb]
    rem_h2h = max(0, max_h2h - played_h2h)
    rem_h2h = min(rem_h2h, rem_a, rem_b)

    b_abs_max_win = border_team["win"] + rem_b
    b_abs_max_rate = calc_rate(b_abs_max_win, border_team["lose"])
    a_cur_min_rate = calc_rate(team_a["win"], team_a["lose"] + rem_a)
    if a_cur_min_rate > b_abs_max_rate:
        return "確定"

    magic = None
    for x in range(0, rem_a + 1):
        forced_b_losses = min(x, rem_h2h)
        b_possible_wins = rem_b - forced_b_losses
        b_max_win = border_team["win"] + b_possible_wins
        b_max_lose = border_team["lose"] + forced_b_losses
        b_max_rate = calc_rate(b_max_win, b_max_lose)

        a_rate = calc_rate(team_a["win"] + x, team_a["lose"] + (rem_a - x))
        if a_rate > b_max_rate:
            magic = x
            break

    if magic is None:
        return "-"
    if magic == 0:
        return "確定"
    return magic

def evaluate_league_clinches(league_standings, h2h_played):
    teams = league_standings
    for i, t in enumerate(teams):
        border_1st = teams[1] if i == 0 else teams[0]
        t["magic_1st"] = calc_clinch_magic_h2h(t, border_1st, h2h_played)

        border_2nd = teams[2] if i < 2 else teams[1]
        t["magic_2nd"] = calc_clinch_magic_h2h(t, border_2nd, h2h_played)

        border_3rd = teams[3] if i < 3 else teams[2]
        t["magic_3rd"] = calc_clinch_magic_h2h(t, border_3rd, h2h_played)

        border_4th = teams[4] if i < 4 else teams[3]
        t["magic_4th"] = calc_clinch_magic_h2h(t, border_4th, h2h_played)

        border_5th = teams[5] if i < 5 else teams[4]
        t["magic_5th"] = calc_clinch_magic_h2h(t, border_5th, h2h_played)
    return teams

def main():
    db = load_db()
    
    # 2026-09-14 から本日までの日付レンジをすべてカバー
    start_date = datetime.date(2026, 9, 14)
    today = datetime.date.today()
    
    game_dates = []
    curr = start_date
    while curr <= today:
        game_dates.append(curr.strftime("%Y-%m-%d"))
        curr += datetime.timedelta(days=1)

    history_snapshots = {}
    for d in game_dates:
        standings, h2h = build_standings_at_date(db, d)
        history_snapshots[d] = {
            "central": evaluate_league_clinches(standings["central"], h2h),
            "pacific": evaluate_league_clinches(standings["pacific"], h2h)
        }

    output = {
        "latest_date": game_dates[-1],
        "available_dates": game_dates,
        "history": history_snapshots
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"スナップショット生成完了: {game_dates[0]} 〜 {game_dates[-1]}")

if __name__ == "__main__":
    main()
