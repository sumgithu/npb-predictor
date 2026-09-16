import datetime
import json
import re
import requests
from bs4 import BeautifulSoup

# ==========================================
# 1. チーム基礎データ（直近80試合の得点・失点）
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
    "DeNA": "ＤｅＮＡ", "横浜": "ＤｅＮＡ", "ソフトバンク": "ソフトバンク",
    "ロッテ": "ロッテ", "楽天": "楽天", "オリックス": "オリックス",
    "日本ハム": "日本ハム", "西武": "西武", "阪神": "阪神",
    "巨人": "巨人", "広島": "広島", "ヤクルト": "ヤクルト", "中日": "中日"
}

# ==========================================
# 2. 先発投手FIPマスタ（代表値）
# ==========================================
PITCHER_FIP = {
    "才木": 2.25, "村上": 2.50, "戸郷": 2.65, "菅野": 2.80, "東": 2.30,
    "有原": 2.80, "伊藤大": 2.60, "モイネロ": 2.10, "小島": 3.40, "早川": 3.10,
    "宮城": 2.40, "今井": 2.70, "西野": 3.20, "種市": 3.15, "佐々木朗": 2.10,
    "岸": 3.30, "則本": 3.40, "藤平": 2.90, "大津": 3.00, "スチュワート": 3.20,
    "エスピノーザ": 3.10, "山下": 2.80, "田嶋": 3.30, "古謝": 3.45, "内": 3.50,
    "カイケル": 3.30, "高橋宏": 1.95, "床田": 2.55, "大瀬良": 2.90, "唐川": 3.50,
    "カスティーヨ": 3.40, "荘司": 3.20, "高橋礼": 3.60, "石川": 3.70
}
DEFAULT_FIP = 3.50

def get_pythagorean_win_rate(runs_scored, runs_allowed, exponent=2.0):
    num = runs_scored ** exponent
    den = (runs_scored ** exponent) + (runs_allowed ** exponent)
    return num / den if den != 0 else 0.500

def log5_matchup(p_a, p_b):
    num = p_a - (p_a * p_b)
    den = p_a + p_b - (2 * p_a * p_b)
    return num / den if den != 0 else 0.500

def clean_pitcher_name(text):
    if not text:
        return "未定"
    cleaned = re.sub(r'(予告先発|投手|先発|勝|敗|Ｓ|H|\[|\]|\:|\s+|\d+)', '', text).strip()
    return cleaned if 1 <= len(cleaned) <= 6 and cleaned not in TEAM_STATS else "未定"

def calculate_win_rate(home_team, away_team, home_starter, away_starter):
    h_stat = TEAM_STATS.get(home_team, {"runs_scored": 300, "runs_allowed": 300})
    a_stat = TEAM_STATS.get(away_team, {"runs_scored": 300, "runs_allowed": 300})

    p_h = get_pythagorean_win_rate(h_stat["runs_scored"], h_stat["runs_allowed"])
    p_a = get_pythagorean_win_rate(a_stat["runs_scored"], a_stat["runs_allowed"])

    base_win = log5_matchup(p_h, p_a)

    fip_h = PITCHER_FIP.get(home_starter, DEFAULT_FIP)
    fip_a = PITCHER_FIP.get(away_starter, DEFAULT_FIP)
    fip_multiplier = (DEFAULT_FIP / fip_h) / (DEFAULT_FIP / fip_a)

    base_odds = base_win / (1.0 - base_win)
    adj_win = (base_odds * fip_multiplier) / (1.0 + (base_odds * fip_multiplier))

    final_h = max(0.05, min(0.95, adj_win + 0.035))
    return round(final_h, 3), round(1.0 - final_h, 3)

def get_exact_pitchers(game_id, headers):
    """試合詳細のテキスト速報ページから確実に『先発投手』のみを取得"""
    text_url = f"https://baseball.yahoo.co.jp/npb/game/{game_id}/text"
    try:
        res = requests.get(text_url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        
        # 責任投手/バッテリー枠、または出場投手一覧枠を検索
        pitchers = []
        
        # 1. バッテリーテーブル（投手の行のみ抽出）
        for tr in soup.find_all("tr"):
            th_td = tr.get_text()
            if "投" in th_td or "バッテリー" in th_td:
                for a in tr.find_all("a", href=re.compile(r"/npb/player/\d+")):
                    name = clean_pitcher_name(a.text)
                    if name != "未定" and name not in pitchers:
                        pitchers.append(name)
                        
        if len(pitchers) >= 2:
            return pitchers[0], pitchers[1]
            
        # 2. 先発表記枠（予告先発やヘッダー）
        head_starters = soup.select(".bb-head01__pitcher, .bb-gameScoreTable__pitcher")
        for s in head_starters:
            name = clean_pitcher_name(s.text)
            if name != "未定" and name not in pitchers:
                pitchers.append(name)
                
        if len(pitchers) >= 2:
            return pitchers[0], pitchers[1]
    except Exception:
        pass
    return "未定", "未定"

def scrape_matchups():
    base_url = "https://baseball.yahoo.co.jp"
    schedule_url = f"{base_url}/npb/schedule/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    matchups = []
    try:
        res = requests.get(schedule_url, headers=headers, timeout=10)
        res.encoding = "utf-8"
        if res.status_code != 200:
            return matchups

        soup = BeautifulSoup(res.text, "html.parser")

        # 1. 「今日」の試合日程枠を特定（最初のセクションのみを抽出して明日以降を遮断）
        today_block = soup.find("section", class_="bb-schedule__day")
        if not today_block:
            # 代替ブロック
            today_block = soup.find("div", class_="bb-schedule") or soup

        cards = today_block.find_all(["section", "li"], class_=lambda c: c and any(x in c for x in ["bb-score", "bb-schedule__item"]))

        for card in cards:
            # 試合IDの取得（/npb/game/2021000000/）
            link = card.find("a", href=re.compile(r"/npb/game/(\d+)/"))
            if not link:
                continue

            game_id_match = re.search(r"/npb/game/(\d+)/", link["href"])
            if not game_id_match:
                continue
            game_id = game_id_match.group(1)

            # チーム特定
            teams = []
            for k in TEAM_NAME_MAP.keys():
                idx = card.text.find(k)
                if idx != -1:
                    teams.append((idx, TEAM_NAME_MAP[k]))
            teams.sort(key=lambda x: x[0])

            unique_teams = []
            for _, t in teams:
                if t not in unique_teams:
                    unique_teams.append(t)

            if len(unique_teams) < 2:
                continue

            away_team, home_team = unique_teams[0], unique_teams[1]

            # 投手名を厳密に取得（バッテリー枠から）
            away_starter, home_starter = get_exact_pitchers(game_id, headers)

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
        print(f"取得エラー: {e}")

    return matchups

def main():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    print(f"[{today_str}] 実行開始")

    matchups = scrape_matchups()
    output_data = {"date": today_str, "matchups": matchups}

    with open("prediction.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"完了: {len(matchups)} 件出力")

if __name__ == "__main__":
    main()
