import datetime
import json
import re
import requests
from bs4 import BeautifulSoup

TOTAL_GAMES = 143

def calculate_rate(w, l):
    decided = w + l
    return round(w / decided, 3) if decided > 0 else 0.000

def get_magic_number(team_a, team_b):
    """
    チームAがチームBを最終勝率で自力で上回るためのマジックナンバーを算出
    """
    rem_a = TOTAL_GAMES - team_a["games"]
    rem_b = TOTAL_GAMES - team_b["games"]

    # チームBが残り全勝した場合の最高勝率
    b_max_win = team_b["win"] + rem_b
    b_max_rate = calculate_rate(b_max_win, team_b["lose"])

    # チームAが残り全勝しても届かない場合（自力消滅）
    a_max_win = team_a["win"] + rem_a
    a_max_rate = calculate_rate(a_max_win, team_a["lose"])
    if a_max_rate < b_max_rate:
        return "-"

    # チームAが何勝すればBの最高勝率を超えるかを探索
    for x in range(0, rem_a + 1):
        target_rate = calculate_rate(team_a["win"] + x, team_a["lose"] + (rem_a - x))
        if target_rate > b_max_rate:
            m = x + 1
            return 0 if m <= 0 else m

    return 1

def evaluate_clinch(team, sorted_teams, target_rank):
    """target_rank（1:優勝, 2:2位以上, 3:3位以上, 4:4位以上, 5:5位以上）判定"""
    competitors = sorted_teams[target_rank:]
    if not competitors:
        return "確定"

    # 下位チームが残り全勝しても届かないか判定
    already_clinched = True
    for comp in competitors:
        comp_rem = TOTAL_GAMES - comp["games"]
        comp_max_rate = calculate_rate(comp["win"] + comp_rem, comp["lose"])
        cur_rate = calculate_rate(team["win"], team["lose"])
        if cur_rate <= comp_max_rate:
            already_clinched = False
            break

    if already_clinched and team["games"] > 0:
        return "確定"

    max_m = 0
    eliminated = True
    for comp in competitors:
        m = get_magic_number(team, comp)
        if m != "-":
            eliminated = False
            if isinstance(m, int) and m > max_m:
                max_m = m

    if eliminated:
        return "-"
    if max_m == 0:
        return "確定"
    return max_m

def parse_int(text, default=0):
    match = re.search(r'\d+', text)
    return int(match.group()) if match else default

def parse_float(text, default=0.0):
    match = re.search(r'\d+\.\d+', text)
    return float(match.group()) if match else default

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
        valid_tables = []
        for tbl in tables:
            text = tbl.get_text()
            if "勝" in text and "敗" in text and "勝率" in text:
                valid_tables.append(tbl)

        league_keys = ["central", "pacific"]

        for idx, tbl in enumerate(valid_tables[:2]):
            l_key = league_keys[idx]
            rows = tbl.find_all("tr")
            rank_counter = 1

            for r in rows:
                cols = [td.get_text().strip() for td in r.find_all(["td", "th"])]
                # ヘッダー行や短すぎる行をスキップ
                if len(cols) < 7 or "球団" in cols[0] or "順位" in cols[0]:
                    continue

                # 球団名（漢字・カナのみ抽出）
                raw_team_name = cols[1] if len(cols) > 1 else cols[0]
                team_match = re.search(r'(阪神|広島|ＤｅＮＡ|DeNA|巨人|ヤクルト|中日|ソフトバンク|日本ハム|ロッテ|楽天|オリックス|西武)', raw_team_name)
                if not team_match:
                    continue
                team_name = team_match.group().replace("DeNA", "ＤｅＮＡ")

                # 数値列の抽出（試合数、勝、敗、分、勝率、差）
                nums = [c for c in cols if re.search(r'^\d+(\.\d+)?$|^-$', c)]
                
                games = parse_int(cols[2]) if len(cols) > 2 else 0
                win = parse_int(cols[3]) if len(cols) > 3 else 0
                lose = parse_int(cols[4]) if len(cols) > 4 else 0
                draw = parse_int(cols[5]) if len(cols) > 5 else 0
                rate = parse_float(cols[6]) if len(cols) > 6 else calculate_rate(win, lose)
                diff = parse_float(cols[7]) if len(cols) > 7 and cols[7] != "-" else 0.0

                leagues[l_key].append({
                    "rank": rank_counter,
                    "team": team_name,
                    "games": games,
                    "win": win,
                    "lose": lose,
                    "draw": draw,
                    "rate": rate,
                    "diff": diff
                })
                rank_counter += 1

    except Exception as e:
        print(f"スクレイピング例外: {e}")

    # 万が一テーブル取得に失敗した場合のフォールバック（空配列ではなく安全な構造を保持）
    return leagues

def main():
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    updated_at_str = jst_now.strftime("%Y-%m-%d %H:%M JST")
    print(f"[{updated_at_str}] 順位表・クリンチナンバー計算開始")

    data = scrape_league_standings()

    result = {
        "updated_at": updated_at_str,
        "central": [],
        "pacific": []
    }

    for l_key in ["central", "pacific"]:
        teams = data.get(l_key, [])
        for t in teams:
            t["magic_1st"] = evaluate_clinch(t, teams, 1)
            t["magic_2nd"] = evaluate_clinch(t, teams, 2)
            t["magic_3rd"] = evaluate_clinch(t, teams, 3)
            t["magic_4th"] = evaluate_clinch(t, teams, 4)
            t["magic_5th"] = evaluate_clinch(t, teams, 5)
            result[l_key].append(t)

    with open("standings.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"standings.json の出力が完了しました。（セ: {len(result['central'])}件, パ: {len(result['pacific'])}件）")

if __name__ == "__main__":
    main()
