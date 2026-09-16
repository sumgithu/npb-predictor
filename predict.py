import datetime
import json
import re
import time
import requests
from bs4 import BeautifulSoup

# ==========================================
# 1. チーム基礎データ（直近80試合の得点・失点サンプル）
# ==========================================
TEAM_STATS = {
    "阪神": {"runs_scored": 320, "runs_allowed": 260},
    "広島": {"runs_scored": 310, "runs_allowed": 290},
    "ＤｅＮＡ": {"runs_scored": 330, "runs_allowed": 310},
    "巨人": {"runs_scored": 340, "runs_allowed": 280},
    "ヤクルト": {"runs_scored": 290, "runs_allowed": 350},
    "中日": {"runs_scored": 250, "runs_allowed": 280},
    "ソフトバンク": {"runs_scored": 360, "runs_allowed": 240},
    "日本ハム": {"runs_scored": 310, "runs_allowed": 290},
    "ロッテ": {"runs_scored": 290, "runs_allowed": 300},
    "楽天": {"runs_scored": 280, "runs_allowed": 320},
    "オリックス": {"runs_scored": 270, "runs_allowed": 300},
    "西武": {"runs_scored": 230, "runs_allowed": 340}
}

TEAM_NAME_MAP = {
    "DeNA": "ＤｅＮＡ",
    "横浜": "ＤｅＮＡ",
    "日本ハム": "日本ハム",
    "日ハム": "日本ハム",
    "ソフトバンク": "ソフトバンク",
    "ロッテ": "ロッテ",
    "楽天": "楽天",
    "オリックス": "オリックス",
    "西武": "西武",
    "阪神": "阪神",
    "巨人": "巨人",
    "広島": "広島",
    "ヤクルト": "ヤクルト",
    "中日": "中日"
}

# ==========================================
# 2. 先発投手FIPマスタ（リーグ平均基準: 3.50）
# ==========================================
PITCHER_FIP = {
    # 主要先発
    "才木": 2.25, "村上": 2.50, "戸郷": 2.65, "菅野": 2.80, "東": 2.30,
    "有原": 2.80, "伊藤大": 2.60, "モイネロ": 2.10, "小島": 3.40, "早川": 3.10,
    "宮城": 2.40, "今井": 2.70, "西野": 3.20, "種市": 3.15, "佐々木朗": 2.10,
    "佐々木": 2.10, "岸": 3.30, "則本": 3.40, "藤平": 2.90, "大津": 3.00,
    "スチュワート": 3.20, "エスピノーザ": 3.10, "山下": 2.80, "田嶋": 3.30,
    "カイケル": 3.30, "古謝": 3.45, "内": 3.50, "高橋礼": 3.60, "石川": 3.70
}
DEFAULT_FIP = 3.50

def get_pythagorean_win_rate(runs_scored, runs_allowed, exponent=2.0):
    numerator = runs_scored ** exponent
    denominator = (runs_scored ** exponent) + (runs_allowed ** exponent)
    return numerator / denominator if denominator != 0 else 0.500

def log5_matchup(prob_a, prob_b):
    numerator = prob_a - (prob_a * prob_b)
    denominator = prob_a + prob_b - (2 * prob_a * prob_b)
    return numerator / denominator if denominator != 0 else 0.500

def clean_pitcher_name(text):
    """イニング表記や不要文字を削ぎ落とし、投手名だけを抽出"""
    if not text:
        return "未定"
    # 回、表、裏、スコア等の文字が含まれている場合は除外
    if re.search(r'(\d+回|[表裏]|試合|速報|終了|中止|LIVE|一球)', text):
        return "未定"
    # 「予告先発」「勝」「敗」などの接頭語・接尾語を削除
    cleaned = re.sub(r'(予告先発|投手|勝|敗|Ｓ|H|\[|\]|\:|\s+)', '', text).strip()
    return cleaned if len(cleaned) >= 2 else "未定"

def calculate_win_rate(home_team, away_team, home_starter, away_starter):
    h_stat = TEAM_STATS.get(home_team, {"runs_scored": 300, "runs_allowed": 300})
    a_stat = TEAM_STATS.get(away_team, {"runs_scored": 300, "runs_allowed": 300})

    p_home = get_pythagorean_win_rate(h_stat["runs_scored"], h_stat["runs_allowed"])
    p_away = get_pythagorean_win_rate(a_stat["runs_scored"], a_stat["runs_allowed"])

    base_home_win_rate = log5_matchup(p_home, p_away)

    fip_home = PITCHER_FIP.get(home_starter, DEFAULT_FIP)
    fip_away = PITCHER_FIP.get(away_starter, DEFAULT_FIP)

    fip_odds_multiplier = (DEFAULT_FIP / fip_home) / (DEFAULT_FIP / fip_away)

    base_odds = base_home_win_rate / (1.0 - base_home_win_rate)
    adjusted_odds = base_odds * fip_odds_multiplier
    adjusted_home_win_rate = adjusted_odds / (1.0 + adjusted_odds)

    final_home_win_rate = adjusted_home_win_rate + 0.035
    final_home_win_rate = max(0.05, min(0.95, final_home_win_rate))
    final_away_win_rate = 1.0 - final_home_win_rate

    return round(final_home_win_rate, 3), round(final_away_win_rate, 3)

def scrape_matchups():
    """スポーツナビ日程から各試合カードを個別取得"""
    url = "https://baseball.yahoo.co.jp/npb/schedule/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    matchups = []
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "utf-8"
        if res.status_code != 200:
            return matchups

        soup = BeautifulSoup(res.text, "html.parser")

        # 試合カード単位（section または li要素）を走査
        cards = soup.find_all(["section", "li"], class_=lambda c: c and any(x in c for x in ["bb-score", "bb-schedule__item"]))

        for card in cards:
            text_content = card.get_text()

            # 1. 含まれる球団名を特定（順序判定）
            detected_teams = []
            for team_key in TEAM_NAME_MAP.keys():
                # 単語として存在するか
                idx = text_content.find(team_key)
                if idx != -1:
                    norm = TEAM_NAME_MAP[team_key]
                    detected_teams.append((idx, norm))

            # テキスト内の出現順にソートし、重複を除去
            detected_teams.sort(key=lambda x: x[0])
            unique_teams = []
            for _, t in detected_teams:
                if t not in unique_teams:
                    unique_teams.append(t)

            if len(unique_teams) < 2:
                continue

            # NPBの通常表記は「ビジター vs ホーム」
            away_team = unique_teams[0]
            home_team = unique_teams[1]

            # 2. 先発投手の抽出（リンクタグまたは指定クラスから取得）
            pitcher_tags = card.find_all(["a", "span", "p"], class_=lambda c: c and any(x in c for x in ["starter", "pitcher", "link"]))
            extracted_pitchers = []
            for pt in pitcher_tags:
                cleaned = clean_pitcher_name(pt.text)
                if cleaned != "未定" and cleaned not in TEAM_STATS and cleaned not in extracted_pitchers:
                    extracted_pitchers.append(cleaned)

            away_starter = extracted_pitchers[0] if len(extracted_pitchers) >= 1 else "未定"
            home_starter = extracted_pitchers[1] if len(extracted_pitchers) >= 2 else "未定"

            home_win, away_win = calculate_win_rate(home_team, away_team, home_starter, away_starter)

            # 重複防止
            if not any(m["home_team"] == home_team and m["away_team"] == away_team for m in matchups):
                matchups.append({
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_starter": home_starter,
                    "away_starter": away_starter,
                    "home_win_rate": home_win,
                    "away_win_rate": away_win
                })

    except Exception as e:
        print(f"スクレイピングエラー: {e}")

    return matchups

def main():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    print(f"[{today_str}] 実行開始")

    matchups = scrape_matchups()

    output_data = {
        "date": today_str,
        "matchups": matchups
    }

    with open("prediction.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"完了: {len(matchups)} カード抽出")

if __name__ == "__main__":
    main()
