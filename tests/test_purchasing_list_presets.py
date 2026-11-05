"""発注の一覧の絞りのタブ (土台の presets。UX の案 3-A。N153)。件数・選ばれた見た目・「すべて」・2 秒ごとの更新の oob。

相手の会社に HTTP を送らない (send=False・つながりなし。requests.post は呼んだら落とす物に差し替える)。取引先と品目は作り物。
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.purchasing import services

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS = (
    ROOT / "src/seizou/core/manual_default/purchasing_purchaseorder_list.html",
    ROOT / "examples/companies/a_machining/manual/purchasing_purchaseorder_list.html",
    ROOT / "examples/companies/b_plating/manual/purchasing_purchaseorder_list.html",
)


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _partner(user, code="S001"):
    return masters_services.create_partner(
        {"code": code, "name": f"作り物の協力企業 {code}", "role": "subcontractor"}, user
    )


def _order(user, partner, item, state):
    """数量 10・単価 100 の明細が 1 行の発注を作り、state (draft / sent / accepted / cancelled) にする。"""
    po = services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    if state in ("sent", "accepted"):
        po = services.mark_sent(po, user)
    if state == "accepted":
        po = services.mark_accepted(po, user)
    if state == "cancelled":
        po = services.cancel_purchase_order(po, user)
    return po


def _tabs(response):
    """タブの (字, 件数, 選ばれたか) の並び。"""
    return [(p["label"], p["count"], p["selected"]) for p in response.context["presets"]]


def _hrefs(response) -> dict:
    return {p["label"]: p["href"] for p in response.context["presets"]}


def _listed(response):
    return [row["obj"].pk for row in response.context["rows"]]


def _presets_tag(body):
    """タブの箱の開きタグ (<div id="list-presets" …>) の字。"""
    start = body.index('<div id="list-presets"')
    return body[start : body.index(">", start)]


@pytest.fixture
def four_orders(user, item):
    """同じ取引先に、作成・送った・相手が受け入れた・取消の 4 件。"""
    partner = _partner(user)
    return {state: _order(user, partner, item, state) for state in ("draft", "sent", "accepted", "cancelled")}


@pytest.mark.django_db
def test_purchase_order_tabs_and_counts(client_logged_in, four_orders):
    response = client_logged_in.get(reverse("purchasing:purchaseorder_list"))
    assert response.status_code == 200
    assert _tabs(response) == [
        ("すべて", 4, True),
        ("作成", 1, False),
        ("送った", 1, False),
        ("相手が受け入れた", 1, False),
    ]
    body = response.content.decode()
    assert body.count('id="list-presets"') == 1
    assert "hx-swap-oob" not in _presets_tag(body)


@pytest.mark.django_db
def test_purchase_order_tab_href_fills_the_frame(client_logged_in, four_orders):
    url = reverse("purchasing:purchaseorder_list")
    hrefs = _hrefs(client_logged_in.get(url))
    assert all(href.startswith("?") for href in hrefs.values())

    response = client_logged_in.get(url + hrefs["送った"])
    assert _listed(response) == [four_orders["sent"].pk]
    assert '<option value="sent" selected>送った</option>' in response.content.decode()
    assert [label for label, _count, selected in _tabs(response) if selected] == ["送った"]

    assert "f_" not in hrefs["すべて"]
    response = client_logged_in.get(url + hrefs["すべて"])
    assert len(_listed(response)) == 4


@pytest.mark.django_db
def test_tab_counts_follow_keyword_but_not_other_filters(client_logged_in, user, item):
    first = _partner(user, "S001")
    second = _partner(user, "S002")
    _order(user, first, item, "draft")
    _order(user, first, item, "sent")
    _order(user, second, item, "sent")
    url = reverse("purchasing:purchaseorder_list")

    # キーワードは件数に効き、タブの link に残る
    response = client_logged_in.get(url, {"q": "S002"})
    assert _tabs(response) == [
        ("すべて", 1, True),
        ("作成", 0, False),
        ("送った", 1, False),
        ("相手が受け入れた", 0, False),
    ]
    assert all("q=S002" in href for href in _hrefs(response).values())

    # 組に無い絞り (取引先) はどのタブも選ばせず、件数に効かない。タブを押すと外れ、件数と並び替えは残る
    response = client_logged_in.get(url, {"f_partner": first.pk, "per": "20", "sort": "-po_no"})
    assert _tabs(response) == [
        ("すべて", 3, False),
        ("作成", 1, False),
        ("送った", 2, False),
        ("相手が受け入れた", 0, False),
    ]
    for href in _hrefs(response).values():
        assert "per=20" in href and "sort=-po_no" in href
        assert "f_partner=" not in href


@pytest.mark.django_db
def test_poll_redraws_tabs_out_of_band(client_logged_in, user, item):
    partner = _partner(user)
    first = _order(user, partner, item, "sent")
    _order(user, partner, item, "sent")
    url = reverse("purchasing:purchaseorder_list")

    def poll():
        """2 秒ごとの更新と同じ要求 (htmx の要求。今の URL の GET を付ける)。"""
        response = client_logged_in.get(url, {"f_status": "sent"}, HTTP_HX_REQUEST="true", HTTP_X_SEIZOU_LIVE="")
        assert response.status_code == 200
        return response

    response = poll()
    body = response.content.decode()
    assert body.count('id="list-presets"') == 1
    assert 'hx-swap-oob="true"' in _presets_tag(body)
    assert "<html" not in body
    assert _tabs(response) == [
        ("すべて", 2, False),
        ("作成", 0, False),
        ("送った", 2, True),
        ("相手が受け入れた", 0, False),
    ]

    services.mark_accepted(first, user)
    assert _tabs(poll()) == [
        ("すべて", 2, False),
        ("作成", 0, False),
        ("送った", 1, True),
        ("相手が受け入れた", 1, False),
    ]


@pytest.mark.django_db
def test_receipt_list_has_no_tabs(client_logged_in):
    response = client_logged_in.get(reverse("purchasing:receipt_list"))
    assert response.status_code == 200
    assert response.context["presets"] == []
    assert 'id="list-presets"' not in response.content.decode()


def test_manual_fragments_mention_the_tabs():
    texts = [path.read_bytes() for path in FRAGMENTS]
    assert texts[1:] == [texts[0], texts[0]]
    for path in FRAGMENTS:
        text = path.read_text(encoding="utf-8")
        # タブの説明は 1 行 (grep -c と同じく行で数える。1 行の中に「タブ」の字は 2 回ある)
        assert sum("タブ" in line for line in text.splitlines()) == 1, path
