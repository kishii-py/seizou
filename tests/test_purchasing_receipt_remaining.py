"""受入の新規登録の数量の欄の下の「受入の残 n」(初見テスト R5 の候補 C64)。名前は作り物だけ。

- 候補の口 (purchasing:purchaseorderline_select) の字は、集計を通し受入の残が 0 より大きい明細だけ「 / 取引先 残り n」で終わる。
  集計の無い str(line) と、受入の一覧の受けすぎの知らせの字は今のまま。
- 画面の全体と小窓の両方で、数量の欄の下に受入の残 (明細が決まらなければ案内の字) を出し、
  明細を選び直したときに字を写す JS (purchasing/receipt-form.js) を 1 回読む。
- 数を比べて断ることはしない (受入の残より多い数も今どおり保存する)。
形は出荷の入力の残り (tests/test_input_screens.py の 4-3 と tests/test_core_input_fixes_1.py の 1) を写した。
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.purchasing import services
from seizou.purchasing.models import Receipt
from tests.test_purchasing_receipt_views import (  # noqa: F401 - fixture を借りる
    client_logged_in,
    fake_post,
    items,
    subcontractor,
    user,
)

RECEIPT_FORM_JS = Path(__file__).resolve().parents[1] / "src" / "seizou" / "purchasing" / "static" / "purchasing" / "receipt-form.js"
NO_LINE_HELP = "発注の明細を選ぶと、受入の残が出ます。"
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


def _order(partner, user, item, qty="10"):  # noqa: F811
    return services.create_purchase_order(
        partner, [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("25")}], user, order_date=timezone.localdate()
    )


def _helptext(body: str, text: str):
    return re.search(rf'<p id="id_qty_helptext"[^>]*>{re.escape(text)}</p>', body)


@pytest.fixture
def received_line(user, subcontractor, items, fake_post):  # noqa: F811
    """数量 10 の明細に 4 を受け入れた後 (受入の残 6)。"""
    line = _order(subcontractor, user, items[0]).lines.get()
    services.receive(line, Decimal("4"), timezone.localdate(), user)
    return line


@pytest.mark.django_db
def test_line_candidates_end_with_partner_and_remaining(client_logged_in, received_line):  # noqa: F811
    data = client_logged_in.get(reverse("purchasing:purchaseorderline_select")).json()

    assert data["items"][0]["text"].endswith(" / 作り物の協力企業 残り 6")
    assert " 残り" not in str(received_line)


@pytest.mark.django_db
def test_receipt_form_shows_remaining_under_qty(client_logged_in, received_line):  # noqa: F811
    response = client_logged_in.get(reverse("purchasing:receipt_create"), {"line": received_line.pk})

    body = response.content.decode()
    assert _helptext(body, "受入の残 6")
    assert body.count("purchasing/receipt-form.js") == 1
    assert "purchasing/receipt_form.html" in [template.name for template in response.templates]


@pytest.mark.django_db
def test_receipt_form_without_line_shows_hint(client_logged_in, received_line):  # noqa: F811
    for params in ({}, {"line": "abc"}, {"line": "999999"}):
        body = client_logged_in.get(reverse("purchasing:receipt_create"), params).content.decode()
        assert NO_LINE_HELP in body, params


@pytest.mark.django_db
def test_redrawn_form_shows_remaining_of_posted_line(client_logged_in, received_line):  # noqa: F811
    response = client_logged_in.post(
        reverse("purchasing:receipt_create"),
        {"line": received_line.pk, "received_on": timezone.localdate().isoformat(), "qty": "", "note": ""},
    )

    assert response.status_code == 200
    assert _helptext(response.content.decode(), "受入の残 6")


@pytest.mark.django_db
def test_over_receipt_still_saves_and_notice_keeps_text(client_logged_in, user, subcontractor, items, fake_post):  # noqa: F811
    line = _order(subcontractor, user, items[0]).lines.get()

    response = client_logged_in.post(
        reverse("purchasing:receipt_create"),
        {"line": line.pk, "received_on": timezone.localdate().isoformat(), "qty": "12", "note": ""},
    )

    assert response.status_code == 302
    assert Receipt.objects.count() == 1
    # 受入の残がマイナスの明細も、比べずにそのまま出す
    again = client_logged_in.get(reverse("purchasing:receipt_create"), {"line": line.pk}).content.decode()
    assert _helptext(again, "受入の残 -2")
    # 受入の一覧の受けすぎの知らせは今の字のまま (「残り」が付かない)
    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    start = body.index('<div class="alert alert-warning alert-soft mb-4" role="alert">')
    notice = body[start : body.index("</div>", start)]
    assert f"{line}: 受入の数 12 が発注の数 10 を超えています。" in notice
    assert "残り" not in notice


@pytest.mark.django_db
def test_receipt_create_modal_reads_the_remaining_js(client_logged_in, received_line):  # noqa: F811
    url = reverse("purchasing:receipt_create") + f"?line={received_line.pk}"

    modal = client_logged_in.get(url, **MODAL)
    assert modal.status_code == 200
    body = modal.content.decode()
    assert "<html" not in body
    assert 'id="id_qty_helptext"' in body
    assert _helptext(body, "受入の残 6")
    assert body.count("purchasing/receipt-form.js") == 1

    # 発注の取消の小窓は読まない (発注の明細の選ぶ欄が無い)
    cancel = client_logged_in.get(
        reverse("purchasing:purchaseorder_cancel", args=[received_line.purchase_order_id]), **MODAL
    )
    assert cancel.status_code == 200
    assert "purchasing/receipt-form.js" not in cancel.content.decode()


def test_receipt_form_js_writes_text_only():
    text = RECEIPT_FORM_JS.read_text(encoding="utf-8")
    for word in ("textContent", "受入の残", "_helptext", NO_LINE_HELP):
        assert word in text, word
    for word in ("innerHTML", "insertAdjacentHTML", "className", "classList.add", "fetch("):
        assert word not in text, word
