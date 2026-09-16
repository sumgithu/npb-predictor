import datetime
import json
import re
import time
import requests
from bs4 import BeautifulSoup

# ==========================================
# 1. チーム基礎データ（直近80試合の得点・失点サンプル）
# ※ 蓄積データに合わせて数値を更新してください
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

# 球団名の表記揺れ吸収用辞書
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
# ※ FIPが低いほど優秀な投手
# ==========================================
PITCHER_FIP = {
    # 阪神
    "才木": 2.25,
    "村上": 2.50,
    "伊藤将": 3.10,
    "西勇": 3.40,
    # 巨人
    "戸郷": 2.65,
    "菅野": 2.80,
    "山﨑伊": 3.05,
    "グリフィン": 2.90,
    # 広島
    "床田": 2.55,
    "大瀬良": 2.90,
    "九里": 3.30,
    "森下": 2.75,
    # DeNA
    "東": 2.30,
    "ジャクソン": 3.20,
    "ケイ": 3.35,
    # ヤクルト
    "高橋": 3.10,
    "小川": 3.70,
    "サイスニード": 3.80,
    # 中日
    "高橋宏": 1.95,
    "小笠原": 3.20,
    "大野": 3.10,
    # パ・リーグ主要投手
    "有原": 2.80,
    "伊藤大": 2.60,
    "モイネロ": 2.10,
    "小島": 3.40,
    "早川": 3.10,
    "宮城": 2.40,
    "今井": 2.70,
}
DEFAULT_FIP = 3.50  # マスタ未登録投手のデフォルト値


def get_pythagorean_win_rate(runs_scored, runs_allowed, exponent=2.0):
    """ピタゴラス勝率（指数2.0）を計算"""
    numerator = runs_scored**exponent
    denominator = (runs_scored**exponent) + (runs_allowed**exponent)
    return numerator / denominator if denominator != 0 else 0.500


def log5_matchup(prob_a, prob_b):
    """Log5法による2チーム間の直接対決勝率を計算"""
    numerator = prob_a - (prob_a * prob_b)
    denominator = prob_a + prob_b - (2 * prob_a * prob_b)
    return numerator / denominator if denominator != 0 else 0.500


def normalize_team_name(name):
    """チーム名を辞書準拠のフォーマットに統一"""
    for k, v in TEAM_NAME_MAP.items():
        if k in name:
            return v
    for team in TEAM_STATS.keys():
        if team in name:
            return team
    return name


def calculate_win_rate(home_team, away_team, home_starter, away_starter):
    """数理モデルに基づいて最終的な勝率を算出"""
    # 1. ピタゴラス勝率の取得
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

    # 2. Log5 による基礎勝率
    base_home_win_rate = log5_matchup(p_home, p_away)

    # 3. 先発投手FIP補正（オッズ比補正）
    fip_home = PITCHER_FIP.get(home_starter, DEFAULT_FIP)
    fip_away = PITCHER_FIP.get(away_starter, DEFAULT_FIP)

    # FIPが低い方が有利になるようオッズを調整
    fip_odds_multiplier = (DEFAULT_FIP / fip_home) / (DEFAULT_FIP / fip_away)

    # 基礎勝率をオッズに変換 -> 補正 -> 勝率に復元
    base_odds = base_home_win_rate / (1.0 - base_home_win_rate)
    adjusted_odds = base_odds * fip_odds_multiplier
    adjusted_home_win_rate = adjusted_odds / (1.0 + adjusted_odds)

    # 4. ホームアドバンテージ付与 (+3.5%)
    final_home_win_rate = adjusted_home_win_rate + 0.035

    # 確率を [0.05, 0.95] の範囲内にクリップ
    final_home_win_rate = max(0.05, min(0.95, final_home_win_rate))
    final_away_win_rate = 1.0 - final_home_win_rate

    return round(final_home_win_rate, 3), round(final_away_win_rate, 3)


def scrape_matchups():
    """スポーツナビから本日の対戦カードと予告先発をスクレイピング"""
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
        sections = soup.find_all("section", class_="bb-score")

        for sec in sections:
            # チーム名取得
            teams = sec.find_all("p", class_="bb-score__team")
            if len(teams) < 2:
                continue

            away_team_raw = teams[0].text.strip()
            home_team_raw = teams[1].text.strip()

            away_team = normalize_team_name(away_team_raw)
            home_team = normalize_team_name(home_team_raw)

            # 予告先発取得
            starters = sec.find_all("span", class_="bb-score__starter")
            away_starter = "未定"
            home_starter = "未定"

            if len(starters) >= 2:
                away_starter = (
                    starters[0].text.replace("予告先発", "").strip()
                )
                home_starter = (
                    starters[1].text.replace("予告先発", "").strip()
                )
            elif len(starters) == 1:
                away_starter = starters[0].text.strip()

            # 勝率計算
            home_win, away_win = calculate_win_rate(
                home_team, away_team, home_starter, away_starter
            )

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

        # 1秒待機（サーバー負荷防止マナー）
        time.sleep(1)

    except Exception as e:
        print(f"スクレイピング例外発生: {e}")

    return matchups


def main():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    print(f"[{today_str}] 予測スクリプト実行開始")

    matchups = scrape_matchups()

    # 試合中止や試合なし日等でスクレイピングできなかった場合のフォールバック用ダミー
    if not matchups:
        print("試合予定が取得できなかったため、サンプルデータを生成します。")
        matchups = []

    output_data = {"date": today_str, "matchups": matchups}

    # JSON出力
    with open("prediction.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print("prediction.json の書き出しが完了しました。")


if __name__ == "__main__":
    main()
