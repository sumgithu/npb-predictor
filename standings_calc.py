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
    チームAがボーダーチーム(team_b)に対して、
    自力で上回る（最終勝率 > team_bの最高可能勝率）ために必要な最小勝利数
    """
    rem_a = TOTAL_GAMES - team_a["games"]
    rem_b = TOTAL_GAMES - team_b["games"]

    # team_b が残り全勝した場合の最高到達成績
    b_max_win = border_team["win"] + rem_b
    b_max_rate = calc_win_rate(b_max_win, border_team["lose"])

    # チームAが残り全勝しても届かない場合（自力可能性なし）
    a_max_win = team_a["win"] + rem_a
    a_max_rate = calc_win_rate(a_max_win, team_a["lose"])
    if a_max_rate < b_max_rate:
        return "-"

    # チームAがすでに上回っている場合（ボーダーチームが全勝しても届かない）
    a_cur_min_rate = calc_win_rate(team_a["win"], team_a["lose"] + rem_a)
    if a_cur_min_rate > b_max_rate:
        return "確定"

    # チームAの残り試合(rem_a)のうち、X勝(rem_a - X敗)で上回れる最小のXを探索
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

def get_target_border_index(target_rank):
    """
    target_rank を確定させるための相手（ボーダーチーム）の順位インデックス(0-based)
    1st(優勝) -> 2位 (index 1)
    2nd(CS本拠地) -> 3位 (index 2)
    3rd(CS進出) -> 4位 (index 3)
    4th -> 5位 (index 4)
    5th(最下位回避) -> 6位 (index 5)
    """
    return target_rank

def parse_val(text):
    clean = text.strip().replace("-", "0.0")
    # 勝率 (.548 または 0.548)
    if re.match(r'^\.?\d+$', clean):
        if clean.startswith('.'):
            clean = '0' + clean
        return float(clean)
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

        tables = soup.find_all("table", class_=lambda c: c and "bb-rankTable" in c)
        if not tables:
            tables = [t for t in soup.find_all("table") if "勝率" in t.get_text()]

        league_keys = ["central", "pacific"]

        for idx, tbl in enumerate(tables[:2]):
            l_key = league_keys[idx]
            tbody = tbl.find("tbody") or tbl
            rows = tbody.find_all("tr")

            rank = 1
            for r in rows:
                cols = [td.get_text().strip() for td in r.find_all(["td", "th"])]
                if len(cols) < 7:
                    continue

                # チーム名検出
                raw_name = cols[1] if len(cols) > 1 else cols[0]
                m = re.search(r'(阪神|広島|ＤｅＮＡ|DeNA|巨人|ヤクルト|中日|ソフトバンク|日本ハム|ロッテ|楽天|オリックス|西武)', raw_name)
                if not m:
                    continue
                team_name = m.group().replace("DeNA", "ＤｅＮＡ")

                # 列番号の特定（試合, 勝, 敗, 分, 勝率, 差）
                nums = []
                for c in cols[2:]:
                    c_clean = c.replace("-", "").strip()
                    if c_clean == "" or re.search(r'^\d+(\.\d+)?$|^\.\d+$', c_clean):
                        nums.append(c)

                if len(nums) < 5:
                    continue

                games = int(nums[0])
                win = int(nums[1])
                lose = int(nums[2])
                draw = int(nums[3])
                
                # 勝率のパース（.548 形式への対応）
                rate_str = nums[4].strip()
                if rate_str.startswith('.'):
                    rate_str = '0' + rate_str
                rate = float(rate_str) if rate_str else calc_win_rate(win, lose)

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

    return leagues

def calculate_all_clinches(teams):
    """各チームの1st〜5thクリンチナンバーを正しく計算"""
    for i, t in enumerate(teams):
        # 1st: CS(優勝) -> 2位チーム(index 1)との比較
        if i == 0:
            t["magic_1st"] = calc_clinch_magic(t, teams[1])
        else:
            t["magic_1st"] = "-"  # 2位以下に優勝マジックは点灯しない

        # 2nd: CS本拠地(2位以上) -> 3位チーム(index 2)との比較
        if i <= 1:
            t["magic_2nd"] = calc_clinch_magic(t, teams[2])
        else:
            t["magic_2nd"] = "-"

        # 3rd: CS進出(3位以上) -> 4位チーム(index 3)との比較
        if i <= 2:
            t["magic_3rd"] = calc_clinch_magic(t, teams[3])
        else:
            t["magic_3rd"] = "-"

        # 4th: 4位以上 -> 5位チーム(index 4)との比較
        if i <= 3:
            t["magic_4th"] = calc_clinch_magic(t, teams[4])
        else:
            t["magic_4th"] = "-"

        # 5th: 最下位回避(5位以上) -> 6位チーム(index 5)との比較
        if i <= 4:
            t["magic_5th"] = calc_clinch_magic(t, teams[5])
        else:
            t["magic_5th"] = "-"

    return teams

def main():
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    updated_at_str = jst_now.strftime("%Y-%m-%d %H:%M JST")
    print(f"[{updated_at_str}] 集計開始")

    data = scrape_league_standings()

    result = {
        "updated_at": updated_at_str,
        "central": calculate_all_clinches(data.get("central", [])),
        "pacific": calculate_all_clinches(data.get("pacific", []))
    }

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("standings.json 更新完了")

if __name__ == "__main__":
    main()
