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

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
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
    """指定日(target_date_str)終了時点の勝敗表および直接対決消化数を復元"""
    records = {}
    all_teams = CENTRAL_TEAMS + PACIFIC_TEAMS
    h2h_played = {t1: {t2: 0 for t2 in all_teams} for t1 in all_teams}

    for t in all_teams:
        records[t] = {"team": t, "games": 0, "win": 0, "lose": 0, "draw": 0}

    # target_date 以前の終了試合のみを集計
    for g in db.get("games", []):
        if g.get("status") == "finished" and g.get("date") <= target_date_str:
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

    # 各リーグごとに勝率順でソート
    def format_league(team_list):
        res = []
        for t in team_list:
            r = records[t]
            r["rate"] = calc_rate(r["win"], r["lose"])
            res.append(r)
        # 勝率降順、勝利数降順でソート
        res.sort(key=lambda x: (x["rate"], x["win"]), reverse=True)
        # ゲーム差計算
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
    """
    直接対決の残り試合数を厳密に考慮した自力確定ナンバー算出
    team_a: 自チーム
    border_team: 比較対象となる境界チーム
    """
    ta, tb = team_a["team"], border_team["team"]
    rem_a = TOTAL_GAMES - team_a["games"]
    rem_b = TOTAL_GAMES - border_team["games"]

    # 残りの直接対決試合数を算出
    is_same_league = (ta in CENTRAL_TEAMS and tb in CENTRAL_TEAMS) or (ta in PACIFIC_TEAMS and tb in PACIFIC_TEAMS)
    max_h2h = GAMES_PER_OPPONENT_INTRA if is_same_league else GAMES_PER_OPPONENT_INTER
    played_h2h = h2h_played[ta][tb]
    rem_h2h = max(0, max_h2h - played_h2h)
    # 残り試合数を超えることはない
    rem_h2h = min(rem_h2h, rem_a, rem_b)

    # 1. すでに確定しているか？（相手Bが残り全勝しても届かない）
    b_abs_max_win = border_team["win"] + rem_b
    b_abs_max_rate = calc_rate(b_abs_max_win, border_team["lose"])
    a_cur_min_rate = calc_rate(team_a["win"], team_a["lose"] + rem_a)
    if a_cur_min_rate > b_abs_max_rate:
        return "確定"

    # 2. チームAが残りX勝（直接対決含む）した際の、相手Bの最高到達勝率を計算
    # 探索: Aの勝利数 x (0 〜 rem_a)
    magic = None
    for x in range(0, rem_a + 1):
        # Aが x 勝したとき、直接対決でのAの勝利数は最大 min(x, rem_h2h)
        # したがって相手Bには最低でも forced_b_losses 敗がつく
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
        return "-"  # 自力消滅
    if magic == 0:
        return "確定"
    return magic

def evaluate_league_clinches(league_standings, h2h_played):
    """リーグ内全球団に対して1st〜5thナンバーを算出"""
    teams = league_standings
    num_teams = len(teams)

    for i, t in enumerate(teams):
        # 1st: CS(優勝) -> 2位チーム(index 1)との直接対決考慮（首位および2位以下も自力可能性を算出）
        border_1st = teams[1] if i == 0 else teams[0]
        t["magic_1st"] = calc_clinch_magic_h2h(t, border_1st, h2h_played)

        # 2nd: CS本拠地(2位以上) -> 3位チーム(index 2)との比較
        border_2nd = teams[2] if i < 2 else teams[1]
        t["magic_2nd"] = calc_clinch_magic_h2h(t, border_2nd, h2h_played)

        # 3rd: CS進出(3位以上) -> 4位チーム(index 3)との比較
        border_3rd = teams[3] if i < 3 else teams[2]
        t["magic_3rd"] = calc_clinch_magic_h2h(t, border_3rd, h2h_played)

        # 4th: 4位以上 -> 5位チーム(index 4)との比較
        border_4th = teams[4] if i < 4 else teams[3]
        t["magic_4th"] = calc_clinch_magic_h2h(t, border_4th, h2h_played)

        # 5th: 最下位回避(5位以上) -> 6位チーム(index 5)との比較
        border_5th = teams[5] if i < 5 else teams[4]
        t["magic_5th"] = calc_clinch_magic_h2h(t, border_5th, h2h_played)

    return teams

def sync_daily_results(db):
    """スポーツナビ速報・日程から直近の試合結果を自動スクレイピングしてDB追記"""
    url = "https://baseball.yahoo.co.jp/npb/schedule/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    added_count = 0

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")

        # 既存ゲームキーの作成 (date_home_away)
        existing_keys = {f"{g['date']}_{g['home']}_{g['away']}" for g in db.get("games", [])}

        cards = soup.find_all(["section", "li"], class_=lambda c: c and any(x in c for x in ["bb-score", "bb-schedule__item"]))
        today_str = datetime.date.today().strftime("%Y-%m-%d")

        for card in cards:
            text = card.get_text()
            if "試合終了" not in text and "終了" not in text:
                continue

            team_nodes = card.select(".bb-score__team, .bb-splitBox__lead")
            raw_teams = []
            for n in team_nodes:
                for k, v in TEAM_MAP.items():
                    if k in n.text and v not in raw_teams:
                        raw_teams.append(v)

            if len(raw_teams) < 2:
                continue

            away, home = raw_teams[0], raw_teams[1]
            key = f"{today_str}_{home}_{away}"

            # スコアの抽出
            scores = re.findall(r'\b\d+\b', card.select_one(".bb-score__score, .bb-scoreTable") .text if card.select_one(".bb-score__score, .bb-scoreTable") else "")
            if len(scores) >= 2:
                away_score = int(scores[0])
                home_score = int(scores[1])
            else:
                continue

            if key not in existing_keys:
                db["games"].append({
                    "date": today_str,
                    "home": home,
                    "away": away,
                    "home_score": home_score,
                    "away_score": away_score,
                    "status": "finished"
                })
                existing_keys.add(key)
                added_count += 1

    except Exception as e:
        print(f"試合結果自動同期エラー: {e}")

    print(f"新規取り込み完了: {added_count} 試合")
    return db

def main():
    print("=== NPB 自律型クリンチナンバー算出エンジン起動 ===")
    db = load_db()
    db = sync_daily_results(db)
    save_db(db)

    # 蓄積されている全日程の日付一覧を抽出
    game_dates = sorted(list({g["date"] for g in db.get("games", []) if g.get("status") == "finished"}))
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    if today_str not in game_dates:
        game_dates.append(today_str)

    history_snapshots = {}

    for d in game_dates:
        standings, h2h = build_standings_at_date(db, d)
        central_evaluated = evaluate_league_clinches(standings["central"], h2h)
        pacific_evaluated = evaluate_league_clinches(standings["pacific"], h2h)

        history_snapshots[d] = {
            "central": central_evaluated,
            "pacific": pacific_evaluated
        }

    output = {
        "latest_date": game_dates[-1] if game_dates else today_str,
        "available_dates": game_dates,
        "history": history_snapshots
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 最新のスナップショットを standings.json にも保存
    latest_snapshot = {
        "updated_at": f"{today_str} JST",
        "central": history_snapshots[game_dates[-1]]["central"],
        "pacific": history_snapshots[game_dates[-1]]["pacific"]
    }
    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(latest_snapshot, f, ensure_ascii=False, indent=2)

    print(f"全 {len(game_dates)} 日分スナップショット生成完了 -> {HISTORY_FILE}")

if __name__ == "__main__":
    main()
