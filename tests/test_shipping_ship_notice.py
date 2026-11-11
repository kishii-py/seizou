"""出荷の新規登録の数量の欄の下の字が、入荷の無い明細では保存の時の赤の知らせと同じ事を言う (依頼書 N421)。

出所: 精査 Q1 (2026-11-10) の番号 1。支給品・預かり品の入荷を確かめる会社 (receiving_required = stop) で、受注より多い数を入れると、
欄の下の字は「保存の時に確かめの知らせが出ます。」と言い、押すと入荷の赤の知らせで止まった (欄の下の字と赤が別の事を言った)。
本体の決め 11-11 14:2x で、欄の下の字は入荷の有無を見て出し分け、赤の知らせと同じ事を言うようにした。

- 出荷の form は、明細を決めて開いた時 (受注の詳細や生産管理ボードから) と止まって描き直した時に、検査の層の preview の最初の止める
  (無ければ最初の知らせる) の文で欄の下の字を始め、数量の欄に data-ship-notice と data-ship-notice-mode を置く。
- 候補の口 (orders:orderline_select) は知らせを返さない。画面の中で明細を選び直した時は、今までどおり保存の時に知らせる
  (本人の決め 11-11 21:1x。候補の口に知らせを足すには、土台を継承した画面が上書きしてよい名前 (R15) を広げる要があるため)。
- 保存の時の赤の知らせは、検査の層が登録の順 (標準のポカよけの R01 が先、入荷の検査が後) に見た最初の止める。
  JS (shipping/shipment-form.js) の字の順は、この順に合わせる (JS の動きは pytest では見られない)。

作り物の利用者と作り物の明細だけを使う (2 社の種に行を足さない)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.core.checks import STOP_HEAD
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.receiving import services as receiving_services
from seizou.receiving.checks import MESSAGE
from seizou.receiving.models import SOURCE_CONSIGNED
from seizou.shipping.models import Shipment

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 欄の下の字の、知らせの文の後に続く字 (shipping/shipment-form.js の STOP_TEXT と WARN_TEXT と同じ字)
STOP_TEXT = "このままでは保存できません。"
WARN_TEXT = "保存の時に確かめの知らせが出ます。"
HELP_HEAD = '<p id="id_qty_helptext" class="mt-2 text-xs text-base-content/60 data-[tone=warning]:text-warning">'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def lines(user):
    """明細 10 と 5 の作り物の受注。2 行目だけに支給品の入荷 (引当) がある。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    work = masters_services.create_item({"part_no": "W-100", "name": "作り物のワーク", "unit": "個"}, user)
    other = masters_services.create_item({"part_no": "W-200", "name": "作り物の板", "unit": "枚"}, user)
    order = orders_services.create_order(
        customer,
        [
            {"item": work, "qty": Decimal("10"), "unit_price": Decimal("100")},
            {"item": other, "qty": Decimal("5"), "unit_price": Decimal("200")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    missing, received = order.lines.get(line_no=1), order.lines.get(line_no=2)
    receiving_services.create_receiving(
        {
            "item": received.item,
            "partner": customer,
            "source": SOURCE_CONSIGNED,
            "qty": Decimal("5"),
            "received_on": ORDER_DATE,
        },
        user,
        order_line=received,
    )
    return missing, received


def _candidates(client, **params) -> dict:
    response = client.get(reverse("orders:orderline_select"), params)
    assert response.status_code == 200
    return {row["id"]: row for row in response.json()["items"]}


def _qty_input(body: str) -> str:
    match = re.search(r'<input[^>]*name="qty"[^>]*>', body)
    assert match, "数量の欄が無い"
    return match.group(0)


def _help_text(body: str) -> str:
    match = re.search(r'<p id="id_qty_helptext"[^>]*>([^<]*)</p>', body)
    assert match, "数量の欄の下の字が無い"
    return match.group(1)


def _post(client, line, qty):
    data = {"order_line": line.pk, "qty": qty, "shipped_at": SHIPPED_AT.isoformat(), "note": ""}
    return client.post(reverse("shipping:shipment_create"), data)


# ---------------------------------------------------------------- (b) 候補の口は知らせを返さない


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["stop", "warn"])
def test_select_does_not_carry_the_notice(settings, logged_in, lines, mode):
    """入荷を確かめる会社でも、候補の口は明細の知らせの鍵を返さず、読みも増やさない (for=shipping を付けても同じ)。"""
    missing, received = lines
    settings.SEIZOU = {"receiving_required": mode}
    url = reverse("orders:orderline_select")
    logged_in.get(url)  # 設定の上書きの控えを先に読んでおく

    with CaptureQueriesContext(connection) as plain:
        rows = _candidates(logged_in)
    with CaptureQueriesContext(connection) as asked:
        same = _candidates(logged_in, **{"for": "shipping"})

    assert set(rows) == set(same) == {missing.pk, received.pk}
    assert all("ship_notice" not in row and "ship_notice_mode" not in row for row in [*rows.values(), *same.values()])
    # 字 (選んだ値の欄の字と「 残り n」) は今のまま
    assert rows[missing.pk]["text"].endswith(" 残り 10")
    assert len(asked) == len(plain)


# ---------------------------------------------------------------- (c) 出荷の form


@pytest.mark.django_db
def test_shipment_form_select_url_is_unchanged(logged_in, lines):
    body = logged_in.get(reverse("shipping:shipment_create")).content.decode()

    # 選ぶ欄の候補の口は今のまま (知らせのための引数を付けない)
    assert f'data-select-url="{reverse("orders:orderline_select")}"' in body
    assert f'data-modal-url="{reverse("orders:orderline_modal", args=[0])}"' in body


@pytest.mark.django_db
def test_shipment_form_says_the_same_as_the_red_notice(settings, logged_in, lines):
    missing, _received = lines
    settings.SEIZOU = {"over_shipment": "warn", "receiving_required": "stop"}
    url = reverse("shipping:shipment_create")

    for headers in ({}, MODAL):  # 画面の全体と、受注の詳細の明細の「出荷する」から開く小窓
        body = logged_in.get(url, {"order_line": missing.pk}, **headers).content.decode()
        assert HELP_HEAD + MESSAGE + STOP_TEXT + "残り 10</p>" in body, headers
        qty = _qty_input(body)
        assert f'data-ship-notice="{MESSAGE}"' in qty, headers
        assert 'data-ship-notice-mode="stop"' in qty, headers
        assert 'data-remaining="10"' in qty and 'data-over-shipment="warn"' in qty, headers
        # 描いたときは黄にしない (data-tone は JS が付ける)
        assert 'data-tone="warning"' not in body, headers

    # 受注より多い数を送ると、赤の知らせは欄の下の字の頭と同じ文で止まる
    stopped = _post(logged_in, missing, "11")
    assert stopped.status_code == 200
    notice = stopped.context["notice"]
    assert notice.code == "receiving_required"
    assert notice.message == STOP_HEAD + MESSAGE
    assert _help_text(stopped.content.decode()).startswith(MESSAGE)
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_shipment_form_warn_notice_says_the_confirm_words(settings, logged_in, lines):
    missing, _received = lines
    settings.SEIZOU = {"receiving_required": "warn"}

    body = logged_in.get(reverse("shipping:shipment_create"), {"order_line": missing.pk}).content.decode()
    assert _help_text(body) == MESSAGE + WARN_TEXT + "残り 10"
    assert 'data-ship-notice-mode="warn"' in _qty_input(body)


@pytest.mark.django_db
def test_shipment_form_without_notice_keeps_the_remaining_only(settings, logged_in, lines):
    missing, received = lines

    settings.SEIZOU = {"receiving_required": "stop"}
    body = logged_in.get(reverse("shipping:shipment_create"), {"order_line": received.pk}).content.decode()
    assert _help_text(body) == "残り 5"
    assert "data-ship-notice" not in _qty_input(body)

    settings.SEIZOU = {"receiving_required": "off"}
    body = logged_in.get(reverse("shipping:shipment_create"), {"order_line": missing.pk}).content.decode()
    assert _help_text(body) == "残り 10"
    assert "data-ship-notice" not in _qty_input(body)

    # 明細が決まっていなければ、今のまま (選ぶと残りの数が出る字。知らせの属性は置かない)
    settings.SEIZOU = {"receiving_required": "stop"}
    body = logged_in.get(reverse("shipping:shipment_create")).content.decode()
    assert _help_text(body) == "受注の明細を選ぶと、残りの数が出ます。"
    assert "data-ship-notice" not in _qty_input(body)


# ---------------------------------------------------------------- (d) 2 つとも止めるときの赤の知らせ


@pytest.mark.django_db
def test_both_stop_red_is_r01_first(settings, logged_in, lines):
    """受注より多い出荷 (R01) と入荷の検査が 2 つとも止めるなら、赤の知らせは登録の順で先の R01 の文。

    JS は、打った数が残りを超え、over_shipment が止めるとき、超えた字を先に出し、入荷の知らせを後ろに足す (この順に合わせる)。
    """
    missing, _received = lines
    settings.SEIZOU = {"over_shipment": "stop", "receiving_required": "stop"}

    stopped = _post(logged_in, missing, "11")
    notice = stopped.context["notice"]
    assert notice.code == "R01"
    assert notice.message.startswith(STOP_HEAD + "出荷の数の合計 (11) が受注の数 (10) を超えます。")
    assert MESSAGE not in notice.message
    assert Shipment.objects.count() == 0

    # 残り以内なら、赤の知らせは入荷の検査の文 (欄の下の字の頭と同じ)
    within = _post(logged_in, missing, "10")
    assert within.context["notice"].code == "receiving_required"
    assert within.context["notice"].message == STOP_HEAD + MESSAGE
