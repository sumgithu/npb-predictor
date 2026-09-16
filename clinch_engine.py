import datetime
import json
import os
import re

TOTAL_GAMES = 143
GAMES_INTRA = 25  # 同一リーグ内対戦総数
GAMES_INTER = 3   # 交流戦対戦総数
DB_FILE = "games_db.json"
HISTORY_FILE = "history_standings.json"

CENTRAL_TEAMS = ["阪神", "巨人", "ＤｅＮＡ", "ヤクルト", "中日", "広島"]
PACIFIC_TEAMS = ["ソフトバンク", "日本ハム", "ロッテ", "楽天", "オリックス", "西武"]

# 2026-09-14 終了時点の確定成績
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

# 2026-09-14 時点での消化済み直接対決マトリクス（セ・リーグ各球団間の実対戦消化数）
# 各カード全25試合制。25からこの数値を引くことで残り試合数が一意に確定します。
BASELINE_H2H_PLAYED = {
    "阪神": {"巨人": 23, "ＤｅＮＡ": 22, "ヤクルト": 21, "中日": 22, "広島": 21},
    "巨人": {"阪神": 23, "ＤｅＮＡ": 23, "ヤクルト": 23, "中日": 22, "広島": 22},
    "ＤｅＮＡ": {"阪神": 22, "巨人": 23, "ヤクルト": 22, "中日": 23, "広島": 22},
    "ヤクルト": {"阪神": 21, "巨人": 23, "ＤｅＮＡ": 22, "中日": 23, "広島": 22},
    "中日": {"阪神": 22, "巨人": 22, "ＤｅＮＡ": 23, "ヤクルト": 23, "広島": 25},
    "広島": {"阪神": 21, "巨人": 22, "ＤｅＮＡ": 22, "ヤクルト": 22, "中日": 25}
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

def calc_win_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def build_standings_at_date(db, target_date_str):
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS
    records = {}
    for t in all_teams:
        b = BASELINE_RECORDS.get(t, {"games": 0, "win": 0, "lose": 0, "draw": 0})
        records[t] = {
            "team": t,
            "games": b["games"],
            "win": b["win"],
            "lose": b["lose"],
            "draw": b["draw"]
        }

    # 直接対決消化数マトリクスの初期化
    h2h_played = {}
    for t1 in all_teams:
        h2h_played[t1] = {}
        for t2 in all_teams:
            if t1 in BASELINE_H2H_PLAYED and t2 in BASELINE_H2H_PLAYED[t1]:
                h2h_played[t1][t2] = BASELINE_H2H_PLAYED[t1][t2]
            else:
                h2h_played[t1][t2] = 21

    # DB内の試合結果を加算
    for g in db.get("games", []):
        g_date = g.get("date", "")
        if g.get("status") == "finished" and "2026-09-14" < g_date <= target_date_str:
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
            r["rate"] = calc_win_rate(r["win"], r["lose"])
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
    """直接対決の残り試合数（25 - 消化数）から厳密に最小自力勝利数を計算"""
    ta = team_a["team"]
    tb = border_team["team"]
    rem_a = TOTAL_GAMES - team_a["games"]
    rem_b = TOTAL_GAMES - border_team["games"]

    # 残りの直接対決試合数（全25試合制より正確に算出）
    played = h2h_played.get(ta, {}).get(tb, 21)
    rem_h2h = max(0, GAMES_INTRA - played)
    rem_h2h = min(rem_h2h, rem_a, rem_b)

    # 1. 相手Bが残り全勝しても届かない（確定）
    b_abs_max_win = border_team["win"] + rem_b
    b_abs_max_rate = calc_win_rate(b_abs_max_win, border_team["lose"])
    a_cur_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)
    if a_cur_min_rate > b_abs_max_rate:
        return "確定"

    # 2. チームAが残り全勝しても相手Bの現在最低保証勝率に届かない（自力消滅）
    a_abs_max_win = team_a["win"] + rem_a
    a_abs_max_rate = calc_win_rate(a_abs_max_win, team_a["lose"])
    b_cur_min_rate = calc_win_rate(border_team["win"], border_team["lose"] + rem_b)
    if a_abs_max_rate < b_cur_min_rate:
        return "-"

    # 3. 最小自力勝利数 X の探索
    magic = None
    for x in range(0, rem_a + 1):
        # Aが x 勝した際、直接対決により相手Bに強制される最小敗戦数
        forced_b_losses = min(x, rem_h2h)
        b_possible_wins = rem_b - forced_b_losses
        b_max_win = border_team["win"] + b_possible_wins
        b_max_lose = border_team["lose"] + forced_b_losses
        b_max_rate = calc_win_rate(b_max_win, b_max_lose)

        a_rate = calc_win_rate(team_a["win"] + x, team_a["lose"] + (rem_a - x))

        if a_rate > b_max_rate:
            magic = x
            break

    if magic is None:
        return "-"
    if magic == 0:
        return "確定"
    return magic

def evaluate_league_clinches(teams, h2h_played):
    for i, t in enumerate(teams):
        rank = i + 1
        for k, key in [(1, "magic_1st"), (2, "magic_2nd"), (3, "magic_3rd"), (4, "magic_4th"), (5, "magic_5th")]:
            if rank <= k:
                border = teams[k]
                t[key] = calc_clinch_magic_h2h(t, border, h2h_played)
            else:
                border = teams[k - 1]
                t[key] = calc_clinch_magic_h2h(t, border, h2h_played)
    return teams

def main():
    db = load_db()
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

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": f"{game_dates[-1]} JST",
            "central": history_snapshots[game_dates[-1]]["central"],
            "pacific": history_snapshots[game_dates[-1]]["pacific"]
        }, f, ensure_ascii=False, indent=2)

    print("クリンチ計算完了")

if __name__ == "__main__":
    main()
