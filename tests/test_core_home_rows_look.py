"""ダッシュボード (core/home.html) の見た目の 3 つ (初見ユーザーテスト R5 の候補 C51): 今日やることの区切りの線の色・納期アラートの列の順・横に並ぶ枠の高さ。

夜のレーンはブラウザを開かないので、home.html と作った CSS (app.css) の字を読んで見る。server も DB も使わない。
形は tests/test_core_inlet_chart.py の test_home_frames_can_shrink (class の字と、app.css にその class の規則があること) にそろえた。
画面の上での色・位置・高さは本体が手で確かめる。
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "seizou"
HOME = SRC / "core" / "templates" / "core" / "home.html"
APP_CSS = SRC / "core" / "static" / "css" / "app.css"

#: class="…" の値。値の中の {% … %} と {{ … }} は引用符を含みうるので、丸ごと 1 つの塊として読む
#: (例 purchasing/_purchaseorder_card.html の class="{% if field.name == "item" %}…")。
CLASS_VALUE = re.compile(r'class="((?:\{%.*?%\}|\{\{.*?\}\}|[^"])*)"', re.S)


def _home():
    return HOME.read_text(encoding="utf-8")


def _app_css():
    return APP_CSS.read_text(encoding="utf-8")


def test_today_lines_use_the_same_color_as_other_lines():
    # (イ) 今日やることの行の間の線: ほかの区切りの線と同じ divide-base-200 (R5 の r5-a-07・r5-b-08。色の class が無いと字と同じ黒になる)。
    tags = re.findall(r'<ul class="([^"]*)" aria-label="\{\{ today\.title \}\}">', _home())
    assert len(tags) == 1
    words = tags[0].split()
    assert "divide-y" in words and "divide-base-200" in words
    assert ":where(.divide-base-200>:not(:last-child)){border-color:var(--color-base-200)}" in _app_css()


def test_every_divide_y_has_a_line_color():
    # (ロ) src の全部の画面で、divide-y の線に色の class (divide-base-…) が同じ class の値にある。語どうしを丸ごと比べる。
    found = 0
    missing = []
    for path in sorted(SRC.rglob("*.html")):
        for value in CLASS_VALUE.findall(path.read_text(encoding="utf-8")):
            words = value.split()
            if "divide-y" not in words:
                continue
            found += 1
            if not any(word.startswith("divide-base-") for word in words):
                missing.append(f"{path.relative_to(SRC)}: {value}")
    # 探し方が壊れて何も見ないまま通るのを防ぐ (数そのものは書かない。ほかの版が divide-y の行を足すため)。
    assert found > 0
    assert missing == []


def _due_alerts():
    return re.search(r'<section[^>]*aria-labelledby="due-alerts-title".*?</section>', _home(), re.S).group(0)


def test_due_alerts_columns_put_status_and_due_next_to_order_no():
    # (ハ) 納期アラートの表は「受注番号・状況・納期・得意先・品番・品名・残」(決め C51-1 の a。R5 の a-blind-04・b-blind-03)。
    section = _due_alerts()
    head = re.search(r"<thead>.*?</thead>", section, re.S).group(0)
    assert re.findall(r"<th[^>]*>([^<]*)</th>", head) == ["受注番号", "状況", "納期", "得意先", "品番", "品名", "残"]
    # 欄の中身も見出しと同じ順 (遅れの行と 7 日以内の行のそれぞれ)。
    order = ["row.order_no", "badge", "row.due_date", "row.customer", "row.item.part_no", "row.item.name", "row.remaining"]
    for loop in ("{% for row in alerts.overdue.rows %}", "{% for row in alerts.soon.rows %}"):
        start = section.index(loop)
        rows = section[start : section.index("{% endfor %}", start)]
        places = [rows.index(word) for word in order]
        assert places == sorted(places), loop


def test_side_by_side_frames_keep_their_own_height():
    # (ニ) 横に並ぶ 2 つの枠は中身の高さ (決め C51-2 の a。R5 の r5-b-09)。今日やることと納期アラートの段、出荷と生産の段の 2 つ。
    html = _home()
    for condition in ("{% if today and alerts is not None %}", "{% if shipping is not None and production is not None %}"):
        tags = re.findall(re.escape(condition) + r'<div class="([^"]*)">', html)
        assert len(tags) == 1, condition
        words = tags[0].split()
        assert "lg:grid-cols-2" in words and "lg:items-start" in words, condition
    assert ".lg\\:items-start{align-items:flex-start}" in _app_css()
