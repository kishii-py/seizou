"""発注の明細の「完了にする」「完了を外す」の画面と入口 (見比べのまとめの M03。本人の決め 11-11 08:4x・09:3x。RULES.md R25。2 段目)。

入口は発注の詳細の明細の ⋮ (完了にする・完了を外す) と、受入の新規登録の欄「残りは受け入れずに完了にする」。
完了にするは終わっていない明細 (印が偽で受入の残が 0 より大きい) だけ、完了を外すは印が真の明細だけに出す。数量まで受け入れて終わった明細には、どちらも出さない。
seizou どうしで送った発注を足りないまま完了にすると、相手に伝わらないので黄の知らせ。確かめの画面には「変更」の案内の 1 行を出す (「変更」の入口が出る発注だけ)。
相手の受け口への HTTP は requests.post を偽の物に差し替える (外へは出ない)。作り物のデータだけを使う。
"""

import json
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrderLine, Receipt
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 11, 2)
DAY = date(2026, 11, 5)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
NO_WRITE = {"department": "off", "write": "on", "money": "off"}
ENV_NAME = "SEIZOU_PARTNER_DONESUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
SENT_NOTE = (
    "相手の seizou には伝わりません。相手にも止めてほしいときは、完了を外してから、"
    "発注の「変更」で数量を受入済みの数に直して送ってください。"
)
CHANGE_GUIDE = "相手にも伝えるときは、完了にせずに発注の「変更」で数量を受入済みの数に直して送ってください。"
BANNED = ("打ち切", "開き直")


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": f"T-{n}00", "name": f"作り物の部品 {n}", "unit": "個"}, user)
        for n in range(1, 4)
    ]


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def fake(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def sent_partner(user, monkeypatch, fake):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    partner = masters_services.create_partner(
        {"code": "S002", "name": "作り物の協力企業 2", "role": "subcontractor"}, user
    )
    connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    return partner


def _po(user, partner, items, qtys, **extra):
    po = services.create_purchase_order(
        partner,
        [
            {"item": item, "qty": Decimal(qty), "unit_price": Decimal("30"), **extra}
            for item, qty in zip(items, qtys, strict=False)
        ],
        user,
        order_date=ORDER_DATE,
    )
    return po, list(po.lines.order_by("line_no"))


@pytest.fixture
def short_line(user, subcontractor, items):
    """数量 5 で 3 を受け入れた、送っていない発注の明細 (受入の残 2)。"""
    _po_obj, (line,) = _po(user, subcontractor, items, ["5"])
    services.receive(line, Decimal("3"), DAY, user)
    return _line(line.pk)


def _line(pk):
    return PurchaseOrderLine.objects.get(pk=pk)


def _urls(line):
    return (
        reverse("purchasing:purchaseorderline_complete", args=[line.pk]),
        reverse("purchasing:purchaseorderline_complete_clear", args=[line.pk]),
    )


def _has_link(body, url):
    """⋮ の項目の link (画面へ移る href と、小窓で開く data-modal-form) があるか (URL の全体を属性の形で見る)。"""
    return f'href="{url}?next=' in body and f'data-modal-form="{url}"' in body


def _detail(client, line):
    return client.get(reverse("purchasing:purchaseorder_detail", args=[line.purchase_order_id])).content.decode()


def _detail_rows(body):
    """発注の詳細の明細の表の行 (<tr data-row-menu> ... </tr>) の HTML の並び。"""
    return re.findall(r"<tr data-row-menu>.*?</tr>", body, re.S)


# ---------------------------------------------------------------- 発注の詳細の明細の ⋮


@pytest.mark.django_db
def test_complete_item_only_for_open_line_and_writer(settings, client, user, short_line):
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)

    body = _detail(client, short_line)
    assert _has_link(body, complete_url)
    assert "完了にする" in body
    assert not _has_link(body, clear_url)

    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    body = _detail(client, short_line)
    assert not _has_link(body, complete_url)
    assert not _has_link(body, clear_url)


@pytest.mark.django_db
def test_no_complete_item_for_cancelled_order(client, user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["5"])
    client.force_login(user)
    complete_url, clear_url = _urls(line)
    assert _has_link(_detail(client, line), complete_url)

    services.cancel_purchase_order(po, user)
    body = _detail(client, line)
    assert not _has_link(body, complete_url)
    assert not _has_link(body, clear_url)


@pytest.mark.django_db
def test_completed_line_shows_only_clear(client, user, short_line):
    services.mark_completed(short_line, user)
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)

    body = _detail(client, short_line)
    assert not _has_link(body, complete_url)
    assert _has_link(body, clear_url)
    assert "完了を外す" in body


@pytest.mark.django_db
def test_line_received_to_qty_has_neither_item(client, user, short_line):
    services.receive(short_line, Decimal("2"), DAY, user)
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)

    body = _detail(client, short_line)
    assert not _has_link(body, complete_url)
    assert not _has_link(body, clear_url)


# ---------------------------------------------------------------- 確かめの画面と保存


@pytest.mark.django_db
def test_complete_confirm_screen_and_save(client, user, short_line):
    client.force_login(user)
    complete_url, _clear_url = _urls(short_line)
    label = f"{short_line.purchase_order.po_no}-1"

    for page in (client.get(complete_url), client.get(complete_url, **MODAL)):
        html = page.content.decode()
        assert page.status_code == 200
        assert "残り 2 は受け入れずに終えます。受け入れた数と金額はそのまま残り、支払の見込みにも残ります。" in html
        assert f"{label} を完了にする" in html
        assert "btn btn-error" not in html
        # 送っていない発注には「変更」の案内を出さない
        assert CHANGE_GUIDE not in html

    modal = client.post(complete_url, {}, **MODAL)
    assert modal.status_code == 204
    saved = json.loads(modal["HX-Trigger"])["seizou:saved"]
    assert saved["message"] == (
        f"{label} を完了にしました。残り 2 は受け入れずに終えます。また受け入れるときは、明細の ⋮ から完了を外してください。"
    )
    assert saved["level"] == "success"
    line = _line(short_line.pk)
    assert line.completed is True
    assert services.remaining_qty(line) == Decimal("0")


@pytest.mark.django_db
def test_clear_confirm_screen_restores_remaining(client, user, short_line):
    services.mark_completed(short_line, user)
    client.force_login(user)
    _complete_url, clear_url = _urls(short_line)
    label = f"{short_line.purchase_order.po_no}-1"

    assert f"{label} の完了を外す" in client.get(clear_url, **MODAL).content.decode()
    modal = client.post(clear_url, {}, **MODAL)
    assert modal.status_code == 204
    assert json.loads(modal["HX-Trigger"])["seizou:saved"]["message"] == (
        f"{label} の完了を外しました。残り 2 をまた受け入れられます。"
    )
    line = _line(short_line.pk)
    assert line.completed is False
    assert services.remaining_qty(line) == Decimal("2")


# ---------------------------------------------------------------- 受入の残の枡の印


@pytest.mark.django_db
def test_remaining_cell_marks(client, user, subcontractor, items):
    _po_obj, (short, filled, received) = _po(user, subcontractor, items, ["5", "5", "5"])
    services.receive(short, Decimal("3"), DAY, user)
    services.mark_completed(short, user)
    services.receive(filled, Decimal("1"), DAY, user)
    services.mark_completed(filled, user)
    services.receive(_line(filled.pk), Decimal("4"), DAY, user)
    services.receive(received, Decimal("5"), DAY, user)
    client.force_login(user)

    rows = _detail_rows(_detail(client, short))
    assert len(rows) == 3
    assert '<span class="badge badge-soft badge-ghost">完了 (受けない 2)</span>' in rows[0]
    assert '<span class="badge badge-soft badge-ghost">完了</span>' in rows[1]
    assert ">完了" not in rows[2]


# ---------------------------------------------------------------- 権限


@pytest.mark.django_db
def test_writer_only_can_complete_and_clear(settings, client, user, short_line):
    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)

    assert client.get(complete_url).status_code == 200
    refused = client.post(complete_url, {}).content.decode()
    assert "発注の編集の権限がありません。" in refused
    assert _line(short_line.pk).completed is False

    services.mark_completed(short_line, user)
    refused = client.post(clear_url, {}).content.decode()
    assert "発注の編集の権限がありません。" in refused
    assert _line(short_line.pk).completed is True


# ---------------------------------------------------------------- 受入の新規登録の「残りは受け入れずに完了にする」


@pytest.mark.django_db
def test_receipt_close_rest_completes_line(client, user, short_line):
    client.force_login(user)
    url = reverse("purchasing:receipt_create")
    label = f"{short_line.purchase_order.po_no}-1"

    page = client.get(url, {"line": short_line.pk}).content.decode()
    assert "残りは受け入れずに完了にする" in page
    assert "この受入の後の残りを受け入れずに、明細を完了にします。" in page

    modal = client.post(
        url, {"line": short_line.pk, "qty": "1", "received_on": DAY.isoformat(), "close_rest": "on"}, **MODAL
    )
    assert modal.status_code == 204
    assert json.loads(modal["HX-Trigger"])["seizou:saved"]["message"] == (
        f"受入を保存しました。{label} を完了にしました。残り 1 は受け入れずに終えます。"
        "また受け入れるときは、明細の ⋮ から完了を外してください。"
    )
    line = _line(short_line.pk)
    assert line.completed is True
    assert services.unfilled_qty(line) == Decimal("1")


@pytest.mark.django_db
def test_receipt_without_close_rest_is_unchanged(client, user, short_line):
    client.force_login(user)
    modal = client.post(
        reverse("purchasing:receipt_create"),
        {"line": short_line.pk, "qty": "1", "received_on": DAY.isoformat()},
        **MODAL,
    )
    assert modal.status_code == 204
    line = _line(short_line.pk)
    assert line.completed is False
    assert services.remaining_qty(line) == Decimal("1")


# ---------------------------------------------------------------- 送った発注と一式の明細


@pytest.mark.django_db
def test_sent_order_shows_change_guide_and_warns(client, user, sent_partner, items, fake):
    po, (line,) = _po(user, sent_partner, items, ["100"])
    services.receive(line, Decimal("98"), DAY, user)
    client.force_login(user)
    complete_url, _clear_url = _urls(line)
    calls = len(fake.calls)

    page = client.get(complete_url, **MODAL).content.decode()
    assert "残り 2 は受け入れずに終えます。" in page
    assert CHANGE_GUIDE in page

    modal = client.post(complete_url, {}, **MODAL)
    assert modal.status_code == 204
    saved = json.loads(modal["HX-Trigger"])["seizou:saved"]
    assert saved["level"] == "warning"
    assert saved["message"] == (
        f"{po.po_no}-1 を完了にしました。残り 2 は受け入れずに終えます。また受け入れるときは、明細の ⋮ から完了を外してください。"
        + SENT_NOTE
    )
    assert len(fake.calls) == calls  # 完了は相手へ送らない
    assert _line(line.pk).completed is True


@pytest.mark.django_db
def test_lump_line_without_amount_is_refused_in_red(client, user, subcontractor, items):
    _po_obj, (line,) = _po(user, subcontractor, items, ["10"], price_basis="lump")
    services.receive(line, Decimal("4"), DAY, user)
    assert Receipt.objects.get(line=line).amount == Decimal("0")
    client.force_login(user)
    complete_url, _clear_url = _urls(line)

    modal = client.post(complete_url, {}, **MODAL)
    assert modal.status_code == 200
    html = modal.content.decode()
    assert 'data-notice="stop"' in html
    assert "保存できません。" in html
    assert "一式の明細で、受け入れた物に一式の金額がまだ載っていないので、完了にできません。" in html
    assert _line(line.pk).completed is False


# ---------------------------------------------------------------- 画面の字


@pytest.mark.django_db
def test_screens_do_not_use_banned_words(client, user, short_line):
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)
    pages = [
        _detail(client, short_line),
        client.get(complete_url).content.decode(),
        client.get(reverse("purchasing:receipt_create"), {"line": short_line.pk}).content.decode(),
    ]
    services.mark_completed(short_line, user)
    pages += [_detail(client, short_line), client.get(clear_url).content.decode()]
    for html in pages:
        for word in BANNED:
            assert word not in html
