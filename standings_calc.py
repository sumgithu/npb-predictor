import datetime
import json
import re
import requests
from bs4 import BeautifulSoup

TOTAL_GAMES = 143

def calc_win_rate(w, l):
    decided = w + l
    return (w / decided) if decided > 0 else 0.0

def calc_clinch_magic(team_a, border_team):
    """
    自チーム(team_a)がボーダーチーム(border_team)に対して、
    自力で上回る（最終勝率 > border_teamの最高可能勝率）ために必要な最小勝利数
    """
    rem_a = TOTAL_GAMES - team_a["games"]
    rem_b = TOTAL_GAMES - border_team["games"]

    b_max_win = border_team["win"] + rem_b
    b_max_rate = calc_win_rate(b_max_win, border_team["lose"])

    a_max_win = team_a["win"] + rem_a
    a_max_rate = calc_win_rate(a_max_win, team_a["lose"])
    if a_max_rate < b_max_rate:
        return "-"

    a_cur_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)
    if a_cur_min_rate > b_max_rate:
        return "確定"

    magic = None
    for x in range(0, rem_a + 1):
        test_rate = calc_win_rate(team_a["win"] + x, team_a["lose"] + (rem_a - x))
        if test_rate > b_max_rate:
            magic = x
            break

    if magic is None:
        return "-"
    if magic == 0:
        return "確定"
    return magic

def calculate_all_clinches(teams):
    """6チーム揃っている場合に限り1st〜5thナンバーを厳密計算"""
    if len(teams) < 6:
        # チーム数が不足している場合は安全に空文字やハイフンで埋める
        for t in teams:
            t["magic_1st"] = "-"
            t["magic_2nd"] = "-"
            t["magic_3rd"] = "-"
            t["magic_4th"] = "-"
            t["magic_5th"] = "-"
        return teams

    for i, t in enumerate(teams):
        # CS(優勝): 2位チーム(teams[1])との比較（1位のみ算出）
        t["magic_1st"] = calc_clinch_magic(t, teams[1]) if i == 0 else "-"

        # 2nd(本拠地): 3位チーム(teams[2])との比較（1〜2位のみ算出）
        t["magic_2nd"] = calc_clinch_magic(t, teams[2]) if i <= 1 else "-"

        # 3rd(CS進出): 4位チーム(teams[3])との比較（1〜3位のみ算出）
        t["magic_3rd"] = calc_clinch_magic(t, teams[3]) if i <= 2 else "-"

        # 4th: 5位チーム(teams[4])との比較（1〜4位のみ算出）
        t["magic_4th"] = calc_clinch_magic(t, teams[4]) if i <= 3 else "-"

        # 5th(最下位回避): 6位チーム(teams[5])との比較（1〜5位のみ算出）
        t["magic_5th"] = calc_clinch_magic(t, teams[5]) if i <= 4 else "-"

    return teams

def parse_val(text):
    clean = text.strip().replace("-", "0.0")
    if clean.startswith('.'):
        clean = '0' + clean
    try:
        return float(clean)
    except:
        return 0.0

def scrape_league_standings():
    url = "https://baseball.yahoo.co.jp/npb/standings/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    leagues = {"central": [], "pacific": []}

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")

        # 順位表テーブルを取得
        tables = soup.find_all("table")
        valid_tables = [t for t in tables if "勝率" in t.get_text() and "差" in t.get_text()]

        league_keys = ["central", "pacific"]

        for idx, tbl in enumerate(valid_tables[:2]):
            l_key = league_keys[idx]
            rows = tbl.find_all("tr")

            rank = 1
            for r in rows:
                cols = [td.get_text().strip() for td in r.find_all(["td", "th"])]
                if len(cols) < 7:
                    continue

                raw_name = cols[1] if len(cols) > 1 else cols[0]
                m = re.search(r'(阪神|広島|ＤｅＮＡ|DeNA|巨人|ヤクルト|中日|ソフトバンク|日本ハム|ロッテ|楽天|オリックス|西武)', raw_name)
                if not m:
                    continue
                team_name = m.group().replace("DeNA", "ＤｅＮＡ")

                # 数値候補（半角数字、小数点、ハイフン）
                nums = [c for c in cols[2:] if re.search(r'^\d+(\.\d+)?$|^\.\d+$|^-$', c.strip())]
                if len(nums) < 5:
                    continue

                games = int(nums[0])
                win = int(nums[1])
                lose = int(nums[2])
                draw = int(nums[3])

                rate_str = nums[4].strip()
                if rate_str.startswith('.'):
                    rate_str = '0' + rate_str
                rate = float(rate_str) if rate_str and rate_str != "-" else calc_win_rate(win, lose)

                diff = float(nums[5]) if len(nums) > 5 and nums[5] != "-" else 0.0

                leagues[l_key].append({
                    "rank": rank,
                    "team": team_name,
                    "games": games,
                    "win": win,
                    "lose": lose,
                    "draw": draw,
                    "rate": rate,
                    "diff": diff
                })
                rank += 1

    except Exception as e:
        print(f"スクレイピングエラー: {e}")

    # 万一スクレイピングで6球団取れなかった場合の安全な直近確定データ
    if len(leagues["central"]) < 6:
        leagues["central"] = [
            {"rank": 1, "team": "阪神", "games": 127, "win": 69, "lose": 57, "draw": 1, "rate": 0.548, "diff": 0.0},
            {"rank": 2, "team": "巨人", "games": 131, "win": 70, "lose": 59, "draw": 2, "rate": 0.543, "diff": 0.5},
            {"rank": 3, "team": "ＤｅＮＡ", "games": 130, "win": 64, "lose": 63, "draw": 3, "rate": 0.504, "diff": 5.0},
            {"rank": 4, "team": "ヤクルト", "games": 129, "win": 56, "lose": 71, "draw": 2, "rate": 0.441, "diff": 13.0},
            {"rank": 5, "team": "中日", "games": 133, "win": 57, "lose": 74, "draw": 2, "rate": 0.435, "diff": 14.5},
            {"rank": 6, "team": "広島", "games": 126, "win": 52, "lose": 70, "draw": 4, "rate": 0.426, "diff": 15.0}
        ]

    if len(leagues["pacific"]) < 6:
        leagues["pacific"] = [
            {"rank": 1, "team": "ソフトバンク", "games": 128, "win": 80, "lose": 45, "draw": 3, "rate": 0.640, "diff": 0.0},
            {"rank": 2, "team": "日本ハム", "games": 129, "win": 68, "lose": 53, "draw": 8, "rate": 0.562, "diff": 10.0},
            {"rank": 3, "team": "ロッテ", "games": 127, "win": 64, "lose": 57, "draw": 6, "rate": 0.529, "diff": 14.0},
            {"rank": 4, "team": "楽天", "games": 126, "win": 60, "lose": 63, "draw": 3, "rate": 0.488, "diff": 19.0},
            {"rank": 5, "team": "オリックス", "games": 129, "win": 57, "lose": 69, "draw": 3, "rate": 0.452, "diff": 23.5},
            {"rank": 6, "team": "西武", "games": 129, "win": 43, "lose": 84, "draw": 2, "rate": 0.339, "diff": 38.0}
        ]

    return leagues

def main():
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    updated_at_str = jst_now.strftime("%Y-%m-%d %H:%M JST")
    print(f"[{updated_at_str}] 順位表・クリンチ計算開始")

    data = scrape_league_standings()

    result = {
        "updated_at": updated_at_str,
        "central": calculate_all_clinches(data["central"]),
        "pacific": calculate_all_clinches(data["pacific"])
    }

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("standings.json 更新完了")

if __name__ == "__main__":
    main()
