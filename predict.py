import datetime
import json
import os
import re
import requests
from bs4 import BeautifulSoup

DB_FILE = "db.json"

PARK_FACTORS = {
    "東京ドーム": 1.08, "神宮": 1.12, "横浜": 1.05, "甲子園": 0.88,
    "バンテリンドーム": 0.82, "マツダスタジアム": 0.96, "エスコンフィールド": 1.02,
    "楽天モバイル": 0.98, "ベルーナドーム": 0.95, "ZOZOマリン": 0.92,
    "京セラD大阪": 0.93, "PayPayドーム": 1.04, "みずほPayPay": 1.04
}

TEAM_NAME_MAP = {
    "DeNA": "ＤｅＮＡ", "横浜": "ＤｅＮＡ", "ソフトバンク": "ソフトバンク",
    "ロッテ": "ロッテ", "楽天": "楽天", "オリックス": "オリックス",
    "日本ハム": "日本ハム", "西武": "西武", "阪神": "阪神",
    "巨人": "巨人", "広島": "広島", "ヤクルト": "ヤクルト", "中日": "中日"
}

TEAM_HOME_PARK = {
    "巨人": "東京ドーム", "ヤクルト": "神宮", "ＤｅＮＡ": "横浜",
    "阪神": "甲子園", "中日": "バンテリンドーム", "広島": "マツダスタジアム",
    "日本ハム": "エスコンフィールド", "楽天": "楽天モバイル", "西武": "ベルーナドーム",
    "ロッテ": "ZOZOマリン", "オリックス": "京セラD大阪", "ソフトバンク": "PayPayドーム"
}

DEFAULT_FIP = 3.50

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"teams": {}, "pitchers": {}}

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def calculate_fip(stats):
    ip = stats.get("ip", 0)
    if ip < 10.0:
        return DEFAULT_FIP
    hr = stats.get("hr", 0)
    bb = stats.get("bb", 0)
    so = stats.get("so", 0)
    return round(((13 * hr) + (3 * bb) - (2 * so)) / ip + 3.10, 2)

def get_pythagorean(rs, ra, pf=1.0):
    adj_rs = rs * pf
    adj_ra = ra / pf
    num = adj_rs ** 2
    den = (adj_rs ** 2) + (adj_ra ** 2)
    return num / den if den != 0 else 0.500

def log5_matchup(p_a, p_b):
    num = p_a - (p_a * p_b)
    den = p_a + p_b - (2 * p_a * p_b)
    return num / den if den != 0 else 0.500

def clean_name(text):
    if not text:
        return ""
    cleaned = re.sub(r'[\s\d\(\)（）:：勝敗ＳH/\[\]]', '', text)
    cleaned = re.sub(r'(予告先発|先発|投手|右投|左投)', '', cleaned).strip()
    return cleaned if 1 <= len(cleaned) <= 6 and cleaned not in TEAM_STATS else ""

def calculate_prediction(home_team, away_team, home_starter, away_starter, db):
    h_team_stat = db["teams"].get(home_team, {"runs_scored": 300, "runs_allowed": 300})
    a_team_stat = db["teams"].get(away_team, {"runs_scored": 300, "runs_allowed": 300})
    
    park_name = TEAM_HOME_PARK.get(home_team, "甲子園")
    pf = PARK_FACTORS.get(park_name, 1.0)

    p_home = get_pythagorean(h_team_stat["runs_scored"], h_team_stat["runs_allowed"], pf)
    p_away = get_pythagorean(a_team_stat["runs_scored"], a_team_stat["runs_allowed"], 1.0 / pf)

    base_win = log5_matchup(p_home, p_away)

    h_p_stat = db["pitchers"].get(home_starter, {})
    a_p_stat = db["pitchers"].get(away_starter, {})

    fip_h = calculate_fip(h_p_stat)
    fip_a = calculate_fip(a_p_stat)

    fip_mult = (DEFAULT_FIP / fip_h) / (DEFAULT_FIP / fip_a)
    base_odds = base_win / (1.0 - base_win)
    adj_win = (base_odds * fip_mult) / (1.0 + (base_odds * fip_mult))

    final_h = max(0.05, min(0.95, adj_win + 0.035))
    return round(final_h, 3), round(1.0 - final_h, 3)

def get_starters_from_stats(game_id, headers):
    """試合詳細の成績ページ(/stats)から1番手投手を抽出"""
    stats_url = f"https://baseball.yahoo.co.jp/npb/game/{game_id}/stats"
    try:
        res = requests.get(stats_url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")

        # 投手成績テーブルを取得
        tables = soup.find_all("table", class_=lambda c: c and ("bb-scoreTable" in c or "bb-statsTable" in c))
        pitchers = []

        for tbl in tables:
            # 最初の行（先発投手）を取得
            tbody = tbl.find("tbody")
            if tbody:
                row = tbody.find("tr")
                if row:
                    link = row.find("a", href=re.compile(r"/npb/player/\d+"))
                    if link:
                        p_name = clean_name(link.text)
                        if p_name:
                            pitchers.append(p_name)

        if len(pitchers) >= 2:
            return pitchers[0], pitchers[1]
    except Exception:
        pass
    return "", ""

def scrape_today_matchups(db):
    url = "https://baseball.yahoo.co.jp/npb/schedule/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    matchups = []

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")

        today_block = soup.find("section", class_="bb-schedule__day") or soup
        cards = today_block.find_all(["section", "li"], class_=lambda c: c and any(x in c for x in ["bb-score", "bb-schedule__item"]))

        for card in cards:
            link = card.find("a", href=re.compile(r"/npb/game/(\d+)/"))
            if not link:
                continue
            game_id = re.search(r"/npb/game/(\d+)/", link["href"]).group(1)

            # チーム特定（左：ビジター、右：ホーム）
            team_nodes = card.select(".bb-score__team, .bb-splitBox__lead")
            raw_teams = []
            for n in team_nodes:
                for k, v in TEAM_NAME_MAP.items():
                    if k in n.text and v not in raw_teams:
                        raw_teams.append(v)

            if len(raw_teams) < 2:
                continue

            away_team = raw_teams[0]
            home_team = raw_teams[1]

            away_starter, home_starter = "", ""

            # 判定1: 日程カード内の予告先発枠
            starter_spans = card.select(".bb-score__starter")
            if len(starter_spans) >= 2:
                away_starter = clean_name(starter_spans[0].text)
                home_starter = clean_name(starter_spans[1].text)

            # 判定2: 試合中・終了後は成績ページ(/stats)から先発を抽出
            if not away_starter or not home_starter:
                s_away, s_home = get_starters_from_stats(game_id, headers)
                away_starter = away_starter or s_away
                home_starter = home_starter or s_home

            away_starter = away_starter if away_starter else "未定"
            home_starter = home_starter if home_starter else "未定"

            # 新規投手の自動登録
            for p in [away_starter, home_starter]:
                if p != "未定" and p not in db["pitchers"]:
                    db["pitchers"][p] = {"ip": 30.0, "so": 25, "bb": 10, "hr": 3}

            h_win, a_win = calculate_prediction(home_team, away_team, home_starter, away_starter, db)

            if not any(m["home_team"] == home_team and m["away_team"] == away_team for m in matchups):
                matchups.append({
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_starter": home_starter,
                    "away_starter": away_starter,
                    "home_win_rate": h_win,
                    "away_win_rate": a_win
                })

    except Exception as e:
        print(f"スクレイピングエラー: {e}")

    return matchups

def main():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    print(f"[{today_str}] 実行開始")

    db = load_db()
    matchups = scrape_today_matchups(db)
    save_db(db)

    output_data = {"date": today_str, "matchups": matchups}

    with open("prediction.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"完了: {len(matchups)} カード出力")

if __name__ == "__main__":
    main()
