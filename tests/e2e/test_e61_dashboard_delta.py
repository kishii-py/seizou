"""E-61 ダッシュボードの「今月の◯◯」のカードに、先月との差の札と日ごとの細い線が出る (依頼書 N348。399 回目。受け渡しの seizou-部品の6案-20261107/指示書.md の 3)。

手順: A 社の最初の画面 (/) を開く。
期待: 数字のカードのうち、題が「今月の」で始まるカード (今月の受注・今月の支払の見込み・今月の入金) だけに、数の横の
「▲ +n 先月より」「▼ −n 先月より」「± 0 先月より」のどれかの札と、数の下の細い線が 1 つずつある。ほかのカードには両方とも無い。
「今月の」のカードは 1 枚以上ある (受注の部品があれば「今月の受注」は必ず出る)。

この試験が見る所: `[data-tour="stats"] > div` の各カードの題 (`p.text-xs.font-medium`)・`[data-stat-delta]` の数と値 (up / down / same) と字・
`svg[data-stat-spark]` の数と値と `polyline` の `points`。線の値 (日の数) は、今月の 1 日から今日までの日の数 = 今日の日。
前提: 会社の時刻 (Asia/Tokyo) と回す機の日付が同じ。日付の変わり目には回さない (線の日の数が 1 つずれる)。
見ない所 (理由): 差の数の値と札の色 (A 社の DB に残る受注の数で変わる。数え方は単体の試験 tests/test_core_dashboard_delta.py が見る)、
線の座標の値 (単体の試験)、(i) の小窓の比べ方の 1 文、金額を見られない人 (金額のカードごと出ない。単体の試験)、B 社 (同じ土台)。
A 社の DB に何も足さない。
"""

import re
from datetime import date

from playwright.sync_api import expect

from .conftest import login, shot

THIS_MONTH = "今月の"
DELTA_VALUES = ("up", "down", "same")
# 札の字。減った時の符号は全角のマイナス (U+2212。core/_stat_delta.html)。
DELTA_TEXT = re.compile("^(▲ \\+|▼ −|± 0).*先月より$")


def test_e61_this_month_cards_have_delta_and_sparkline(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/")
    cards = page_a.locator('[data-tour="stats"] > div')
    expect(cards.first).to_be_visible()
    today = date.today().day
    this_month_cards = 0
    for index in range(cards.count()):
        card = cards.nth(index)
        title = card.locator("p.text-xs.font-medium").first.inner_text().strip()
        delta = card.locator("[data-stat-delta]")
        spark = card.locator("svg[data-stat-spark]")
        if not title.startswith(THIS_MONTH):
            expect(delta, f"「{title}」のカードに札がある").to_have_count(0)
            expect(spark, f"「{title}」のカードに線がある").to_have_count(0)
            continue
        this_month_cards += 1
        expect(delta, f"「{title}」のカードに札が 1 つでない").to_have_count(1)
        assert delta.get_attribute("data-stat-delta") in DELTA_VALUES, f"「{title}」の札の値"
        text = " ".join(delta.inner_text().split())
        assert DELTA_TEXT.fullmatch(text), f"「{title}」の札の字が形に合わない: {text!r}"
        expect(spark, f"「{title}」のカードに線が 1 つでない").to_have_count(1)
        expect(spark).to_have_attribute("data-stat-spark", str(today))
        points = spark.locator("polyline").get_attribute("points") or ""
        assert points.strip(), f"「{title}」の線の points が空"
    assert this_month_cards >= 1, "「今月の」で始まるカードが無い (受注の部品があれば「今月の受注」が出る)"
    shot(page_a, "E-61", "a-dashboard-delta")
