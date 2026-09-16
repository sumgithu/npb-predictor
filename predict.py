import datetime
import json
import os
import re
import requests
from bs4 import BeautifulSoup

DB_FILE = "db.json"

# 球場別パークファクター（得点補正係数）
PARK_FACTORS = {
    "東京ドーム": 1.08,
    "神宮": 1.12,
    "横浜": 1.05,
    "甲子園": 0.88,
    "バンテリンドーム": 0.82,
    "マツダスタジアム": 0.96,
    "エスコンフィールド": 1.02,
    "楽天モバイル": 0.98,
    "ベルーナドーム": 0.95,
    "ZOZOマリン": 0.92,
    "京セラD大阪": 0.93,
    "PayPayドーム": 1.04,
    "みずほPayPay": 1.04
}

TEAM_NAME_MAP = {
    "DeNA": "ＤｅＮＡ", "横浜": "ＤｅＮＡ", "ソフトバンク": "ソフトバンク",
    "ロッテ": "ロッテ", "楽天": "楽天", "オリックス": "オリックス",
    "日本ハム": "日本ハム", "西武": "西武", "阪神": "阪神",
    "巨人": "巨人", "広島": "広島", "ヤクルト": "ヤクルト", "中日": "中日"
}

# 球団本拠地マッピング
TEAM_HOME_PARK = {
    "巨人": "東京ドーム", "ヤクルト": "神宮", "ＤｅＮＡ": "横浜",
    "阪神": "甲子園", "中日": "バンテリンドーム", "広島": "マツダスタジアム",
    "日本ハム": "エスコンフィールド", "楽天": "楽天モバイル", "西武": "ベルーナドーム",
    "ロッテ": "ZOZOマリン", "オリックス": "京セラD大阪", "ソフトバンク": "PayPayドーム"
}

DEFAULT_FIP = 3.50

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"teams": {}, "pitchers": {}}

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def calculate_fip(stats):
    """投球回、被本塁打、与四球、奪三振からFIPを算出"""
    ip = stats.get("ip", 0)
    if ip < 10.0:  # サンプルが極端に少ない場合はリーグ平均に回帰
        return DEFAULT_FIP
    hr = stats.get("hr", 0)
    bb = stats.get("bb", 0)
    so = stats.get("so", 0)
    # FIP定数 = 3.10
    fip_val = ((13 * hr) + (3 * bb) - (2 * so)) / ip + 3.10
    return round(fip_val, 2)

def get_pythagorean(rs, ra, pf=1.0):
    """球場PFを加味したピタゴラス勝率（指数2.0）"""
    adj_rs = rs * pf
    adj_ra = ra / pf
    num = adj_rs ** 2
    den = (adj_rs ** 2) + (adj_ra ** 2)
    return num / den if den != 0 else 0.500

def log5_matchup(p_a, p_b):
    num = p_a - (p_a * p_b)
    den = p_a + p_b - (2 * p_a * p_b)
    return num / den if den != 0 else 0.500

def calculate_prediction(home_team, away_team, home_starter, away_starter, db):
    # 1. チーム直近得失点と球場PF
    h_team_stat = db["teams"].get(home_team, {"runs_scored": 300, "runs_allowed": 300})
    a_team_stat = db["teams"].get(away_team, {"runs_scored": 300, "runs_allowed": 300})
    
    park_name = TEAM_HOME_PARK.get(home_team, "甲子園")
    pf = PARK_FACTORS.get(park_name, 1.0)

    p_home = get_pythagorean(h_team_stat["runs_scored"], h_team_stat["runs_allowed"], pf)
    p_away = get_pythagorean(a_team_stat["runs_scored"], a_team_stat["runs_allowed"], 1.0 / pf)

    base_win = log5_matchup(p_home, p_away)

    # 2. 蓄積DBからの先発投手FIP算出
    h_p_stat = db["pitchers"].get(home_starter, {})
    a_p_stat = db["pitchers"].get(away_starter, {})

    fip_h = calculate_fip(h_p_stat)
    fip_a = calculate_fip(a_p_stat)

    # FIPオッズ比補正
    fip_mult = (DEFAULT_FIP / fip_h) / (DEFAULT_FIP / fip_a)
    base_odds = base_win / (1.0 - base_win)
    adj_win = (base_odds * fip_mult) / (1.0 + (base_odds * fip_mult))

    # 3. ホーム球団に +3.5% 付与
    final_h = max(0.05, min(0.95, adj_win + 0.035))
    return round(final_h, 3), round(1.0 - final_h, 3)

def scrape_today_matchups(db):
    """本日の試合（ビジター/ホームの厳格分離）と予告先発の抽出"""
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

            # 日本プロ野球のテーブル表記: 常に【左側/1番目がビジター】【右側/2番目がホーム】
            team_nodes = card.select(".bb-score__team, .bb-splitBox__lead")
            raw_teams = []
            for n in team_nodes:
                for k, v in TEAM_NAME_MAP.items():
                    if k in n.text and v not in raw_teams:
                        raw_teams.append(v)

            if len(raw_teams) < 2:
                continue

            # 厳密に左＝ビジター、右＝ホーム
            away_team = raw_teams[0]
            home_team = raw_teams[1]

            # 試合詳細のトップから先発投手を特定
            top_url = f"https://baseball.yahoo.co.jp/npb/game/{game_id}/top"
            res_top = requests.get(top_url, headers=headers, timeout=5)
            soup_top = BeautifulSoup(res_top.text, "html.parser")

            pitcher_tags = soup_top.select(".bb-head01__pitcher")
            away_starter, home_starter = "未定", "未定"
            if len(pitcher_tags) >= 2:
                away_starter = re.sub(r'[\s\d\(\)（）:：勝敗ＳH]', '', pitcher_tags[0].text).strip()
                home_starter = re.sub(r'[\s\d\(\)（）:：勝敗ＳH]', '', pitcher_tags[1].text).strip()

            # 新規投手が検知された場合は初期値をDBに自動登録
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
    
    # 新規投手等が登録されたDBを再保存
    save_db(db)

    output_data = {
        "date": today_str,
        "matchups": matchups
    }

    with open("prediction.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"完了: {len(matchups)} カード出力、db.json更新完了")

if __name__ == "__main__":
    main()
