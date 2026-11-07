"""一覧の期間の絞りの近道「今日・今週・今月・先月」(受け渡しの seizou-部品の6案-20261107/指示書.md の 1。本人の決め 11-07 18:2x)。

近道の日付はサーバーが会社の時刻で決める (FilterSpec.preset_range・date_presets)。ボタンは core/_date_presets.html が描き、
押す動きは static の js/seizou-date-presets.js (JS は動かさず字を見る。ブラウザの確かめは本体の手)。
一覧の HTML は Django の test client で開く (受注と指示書の一覧 = 期間の絞りを持つ 2 つ)。名前は作り物だけ。
"""

import re
from datetime import date
from pathlib import Path

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

import seizou.core
from seizou.core.views import FilterSpec
from seizou.masters.models import Item
from seizou.orders.models import Order

JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-date-presets.js"
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")
PRESSED = '<button type="button" class="btn btn-xs btn-soft btn-primary" aria-pressed="true"'
PLAIN = '<button type="button" class="btn btn-xs btn-ghost text-base-content/70" aria-pressed="false"'
TODAY = date(2026, 11, 7)  # 土曜日


@pytest.fixture
def fixed_today(monkeypatch):
    """会社の時刻の今日を 2026-11-07 (土) に固定する (date_presets が timezone.localdate を読む)。"""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    return TODAY


# ---- 1. 日付の計算 ----


@pytest.mark.parametrize(
    ("today", "key", "start", "end"),
    [
        (date(2026, 11, 7), "today", date(2026, 11, 7), date(2026, 11, 7)),
        (date(2026, 11, 4), "week", date(2026, 11, 2), date(2026, 11, 8)),  # 水曜 → 月曜から日曜
        (date(2026, 11, 2), "week", date(2026, 11, 2), date(2026, 11, 8)),  # 月曜
        (date(2026, 11, 8), "week", date(2026, 11, 2), date(2026, 11, 8)),  # 日曜 (週の終わり)
        (date(2026, 1, 1), "week", date(2025, 12, 29), date(2026, 1, 4)),  # 年またぎ
        (date(2026, 11, 7), "month", date(2026, 11, 1), date(2026, 11, 30)),
        (date(2026, 2, 10), "month", date(2026, 2, 1), date(2026, 2, 28)),
        (date(2028, 2, 10), "month", date(2028, 2, 1), date(2028, 2, 29)),  # うるう年
        (date(2026, 12, 31), "month", date(2026, 12, 1), date(2026, 12, 31)),
        (date(2026, 11, 7), "last_month", date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 1, 15), "last_month", date(2025, 12, 1), date(2025, 12, 31)),  # 年またぎ
        (date(2026, 3, 31), "last_month", date(2026, 2, 1), date(2026, 2, 28)),  # 末日から先月
    ],
)
def test_preset_range(today, key, start, end):
    assert FilterSpec.preset_range(key, today) == (start, end)


def test_unknown_preset_is_refused():
    with pytest.raises(ValueError, match="知らない期間の近道"):
        FilterSpec.preset_range("yesterday", TODAY)


def test_date_presets_are_four_iso_strings_in_order(fixed_today):
    presets = FilterSpec.date_presets("", "")

    assert [p["key"] for p in presets] == ["today", "week", "month", "last_month"]
    assert [p["label"] for p in presets] == ["今日", "今週", "今月", "先月"]
    assert [(p["start"], p["end"]) for p in presets] == [
        ("2026-11-07", "2026-11-07"),
        ("2026-11-02", "2026-11-08"),
        ("2026-11-01", "2026-11-30"),
        ("2026-10-01", "2026-10-31"),
    ]
    assert not any(p["selected"] for p in presets)
    assert [p["selected"] for p in FilterSpec.date_presets("2026-11-01", "2026-11-30")] == [False, False, True, False]
    # 片方だけ同じでは選んだ印にしない
    assert not any(p["selected"] for p in FilterSpec.date_presets("2026-11-01", ""))
    assert FilterSpec.date_presets("2026-11-07", "2026-11-07", today=date(2026, 11, 8))[0]["selected"] is False


# ---- 2. 絞りの材料 (FilterSpec.context) ----


@pytest.mark.django_db
def test_date_range_context_has_presets_and_number_range_does_not(fixed_today):
    ordered = FilterSpec(Order, {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"})
    price = FilterSpec(Item, {"name": "price", "label": "単価", "kind": "number_range", "field": "standard_price"})

    item = ordered.context(QueryDict("f_ordered_from=2026-10-01&f_ordered_to=2026-10-31"))
    assert [p["selected"] for p in item["presets"]] == [False, False, False, True]
    assert item["value_from"] == "2026-10-01" and item["value_to"] == "2026-10-31"
    # 読めない字の欄は空なので、どの近道も選んだ印にならない
    assert not any(p["selected"] for p in ordered.context(QueryDict("f_ordered_from=2026-10-01&f_ordered_to=xx"))["presets"])
    assert "presets" not in price.context(QueryDict(""))


# ---- 3. 一覧の HTML (受注と指示書の一覧で同じ形) ----


def presets_box(html: str, name: str) -> str:
    return re.search(rf'<div id="list-f-{name}-presets".*?</div>', html, re.S).group(0)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "name", "label"),
    [("orders:order_list", "ordered", "受注日"), ("workorders:workorder_list", "issued", "発行日")],
)
def test_lists_show_the_same_presets_under_the_date_fields(client_logged_in, fixed_today, url_name, name, label):
    html = client_logged_in.get(reverse(url_name)).content.decode()

    assert f'data-date-range="{name}"' in html
    box = presets_box(html, name)
    assert f'class="mt-2 flex flex-wrap gap-1" role="group" aria-label="{label}の近道">' in box
    assert "hx-swap-oob" not in box
    assert box.count("<button") == 4 and box.count(PLAIN) == 4 and PRESSED not in box
    assert 'data-preset-start="2026-11-07" data-preset-end="2026-11-07">今日</button>' in box
    assert 'data-preset-start="2026-11-02" data-preset-end="2026-11-08">今週</button>' in box
    assert 'data-preset-start="2026-11-01" data-preset-end="2026-11-30">今月</button>' in box
    assert 'data-preset-start="2026-10-01" data-preset-end="2026-10-31">先月</button>' in box
    # 近道は 2 つの日付の欄 (始め・終わり) の後、同じ期間の枠の中
    frame = re.search(rf'<div[^>]*data-date-range="{name}">.*?</div>\s*</div>', html, re.S).group(0)
    assert frame.count('type="date"') == 2
    assert frame.index('type="date"') < frame.index(f'id="list-f-{name}-presets"')


@pytest.mark.django_db
def test_matching_dates_mark_the_preset_and_rows_partial_redraws_it(client_logged_in, fixed_today):
    url = reverse("orders:order_list")
    html = client_logged_in.get(url, {"f_ordered_from": "2026-11-01", "f_ordered_to": "2026-11-30"}).content.decode()

    box = presets_box(html, "ordered")
    assert box.count(PRESSED) == 1 and box.count(PLAIN) == 3
    assert f'{PRESSED} data-preset-start="2026-11-01" data-preset-end="2026-11-30">今月</button>' in box

    # 行の部分の応答 (欄の change で htmx が取り直す物) は、近道の箱を oob で描き直して選んだ印を欄の値に合わせる
    partial = client_logged_in.get(
        url, {"f_ordered_from": "2026-10-01", "f_ordered_to": "2026-10-31"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="rows"
    ).content.decode()
    assert "<html" not in partial
    box = presets_box(partial, "ordered")
    assert 'role="group" aria-label="受注日の近道" hx-swap-oob="true">' in box
    assert f'{PRESSED} data-preset-start="2026-10-01" data-preset-end="2026-10-31">先月</button>' in box
    assert box.count(PRESSED) == 1
    # 納期の近道も同じ応答で描き直す (受注の一覧は期間の絞りが 2 つ)
    assert 'id="list-f-due-presets"' in partial and partial.count('hx-swap-oob="true"') >= 2


# ---- 4. JS の字と読み込み ----


def test_date_presets_js_is_small_and_plain():
    text = JS.read_text(encoding="utf-8")

    assert len(text.splitlines()) <= 40
    for word in (
        'addEventListener("click"',
        "button[data-preset-start][data-preset-end]",
        "[data-date-range]",
        'querySelectorAll(\'input[type="date"]\')',
        '"data-preset-start"',
        '"data-preset-end"',
        'new Event("change", { bubbles: true })',
        "defaultPrevented",
    ):
        assert word in text, word
    for word in ("classList", "className", "fetch(", "XMLHttpRequest", "new Date", "innerHTML", "aria-pressed"):
        assert word not in text, word


@pytest.mark.django_db
def test_base_loads_date_presets_js_with_defer(client_logged_in):
    body = client_logged_in.get(reverse("core:home")).content.decode()

    scripts = [src for src in SCRIPT_SRC.findall(body) if src.endswith("js/seizou-date-presets.js")]
    assert len(scripts) == 1
    tag = re.search(r"<script [^>]*js/seizou-date-presets\.js[^>]*>", body).group(0)
    assert "defer" in tag
