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
    "西武": {"runs_scored": 230, "runs_allowed": 340},
}

TEAM_NAME_MAP = {
    "DeNA": "ＤｅＮＡ",
    "横浜": "ＤｅＮＡ",
    "ソフトバンクホークス": "ソフトバンク",
    "タイガース": "阪神",
    "ジャイアンツ": "巨人",
    "カープ": "広島",
    "スワローズ": "ヤクルト",
    "ドラゴンズ": "中日",
    "ファイターズ": "日本ハム",
    "マリーンズ": "ロッテ",
    "イーグルス": "楽天",
    "バファローズ": "オリックス",
    "ライオンズ": "西武",
}

# ==========================================
# 2. 先発投手FIPマスタ（リーグ平均基準: 3.50）
# ==========================================
PITCHER_FIP = {
    "才木": 2.25,
    "村上": 2.50,
    "戸郷": 2.65,
    "菅野": 2.80,
    "東": 2.30,
    "有原": 2.80,
    "伊藤大": 2.60,
    "モイネロ": 2.10,
    "小島": 3.40,
    "早川": 3.10,
    "宮城": 2.40,
    "今井": 2.70,
    "西野": 3.20,
    "種市": 3.15,
    "佐々木朗": 2.10,
    "岸": 3.30,
    "則本": 3.40,
    "藤平": 2.90,
    "大津": 3.00,
    "スチュワート": 3.20,
    "エスピノーザ": 3.10,
    "山下": 2.80,
    "田嶋": 3.30,
}
DEFAULT_FIP = 3.50


def get_pythagorean_win_rate(runs_scored, runs_allowed, exponent=2.0):
    numerator = runs_scored**exponent
    denominator = (runs_scored**exponent) + (runs_allowed**exponent)
    return numerator / denominator if denominator != 0 else 0.500


def log5_matchup(prob_a, prob_b):
    numerator = prob_a - (prob_a * prob_b)
    denominator = prob_a + prob_b - (2 * prob_a * prob_b)
    return numerator / denominator if denominator != 0 else 0.500


def normalize_team_name(name):
    clean_name = re.sub(r"[\s\d\(\)]+", "", name)
    for k, v in TEAM_NAME_MAP.items():
        if k in clean_name:
            return v
    for team in TEAM_STATS.keys():
        if team in clean_name:
            return team
    return clean_name


def calculate_win_rate(home_team, away_team, home_starter, away_starter):
    h_stat = TEAM_STATS.get(
        home_team, {"runs_scored": 300, "runs_allowed": 300}
    )
    a_stat = TEAM_STATS.get(
        away_team, {"runs_scored": 300, "runs_allowed": 300}
    )

    p_home = get_pythagorean_win_rate(
        h_stat["runs_scored"], h_stat["runs_allowed"]
    )
    p_away = get_pythagorean_win_rate(
        a_stat["runs_scored"], a_stat["runs_allowed"]
    )

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
    """スポーツナビ日程から試合中・試合後も含めて全カード・先発を抽出"""
    url = "https://baseball.yahoo.co.jp/npb/schedule/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }

    matchups = []
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "utf-8"
        if res.status_code != 200:
            return matchups

        soup = BeautifulSoup(res.text, "html.parser")

        # 試合カード全体を広く捕捉（試合前・試合中・終了後の全セクション）
        game_sections = soup.find_all(
            ["section", "li", "div"],
            class_=lambda c: c
            and ("bb-score" in c or "bb-schedule__item" in c),
        )

        for sec in game_sections:
            # チーム名の検出
            team_tags = sec.find_all(
                ["p", "span", "div"],
                class_=lambda c: c
                and any(
                    x in c
                    for x in [
                        "bb-score__team",
                        "bb-gameScoreTable__data--team",
                        "bb-splitBox__lead",
                    ]
                ),
            )
            raw_teams = [
                t.text.strip()
                for t in team_tags
                if any(k in t.text for k in TEAM_STATS.keys())
            ]

            # チームが重複して取れた場合のユニーク化・順序維持
            seen = set()
            valid_teams = []
            for t in raw_teams:
                norm = normalize_team_name(t)
                if norm in TEAM_STATS and norm not in seen:
                    seen.add(norm)
                    valid_teams.append(norm)

            if len(valid_teams) < 2:
                continue

            away_team = valid_teams[0]
            home_team = valid_teams[1]

            # 先発投手の検出（予告先発 or スコアボードの先発名）
            starter_tags = sec.find_all(
                ["span", "p", "a"],
                class_=lambda c: c
                and any(
                    x in c
                    for x in [
                        "bb-score__starter",
                        "bb-gameScoreTable__pitcher",
                        "bb-score__link",
                    ]
                ),
            )
            starters = [
                re.sub(r"(予告先発|勝|敗|Ｓ|\[|\]|\:)", "", s.text).strip()
                for s in starter_tags
                if s.text.strip()
            ]

            away_starter = starters[0] if len(starters) >= 1 else "未定"
            home_starter = starters[1] if len(starters) >= 2 else "未定"

            home_win, away_win = calculate_win_rate(
                home_team, away_team, home_starter, away_starter
            )

            # 重複カード追加防止
            if not any(
                m["home_team"] == home_team and m["away_team"] == away_team
                for m in matchups
            ):
                matchups.append(
                    {
                        "home_team": home_team,
                        "away_team": away_team,
                        "home_starter": home_starter,
                        "away_starter": away_starter,
                        "home_win_rate": home_win,
                        "away_win_rate": away_win,
                    }
                )

    except Exception as e:
        print(f"スクレイピング例外: {e}")

    return matchups


def main():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    print(f"[{today_str}] 予測スクリプト実行開始")

    matchups = scrape_matchups()

    # ダミーデータは廃止し、取得カードのみ出力
    output_data = {"date": today_str, "matchups": matchups}

    with open("prediction.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(
        f"prediction.json の書き出し完了（検出試合数: {len(matchups)}件）"
    )


if __name__ == "__main__":
    main()
