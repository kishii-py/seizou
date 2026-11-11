"""受注の明細の「完了にする」「完了を外す」の画面と入口 (見比べのまとめの M03。本人の決め 11-11 08:4x・09:3x。RULES.md R25。2 段目)。

入口は受注の詳細の明細の ⋮ (完了にする・完了を外す) と生産管理ボードの行の ⋮ (完了にする) と、出荷の新規登録の欄「残りは出さずに完了にする」。
完了にするは終わっていない明細 (印が偽で残が 0 より大きい) だけ、完了を外すは印が真の明細だけに出す。数量まで出荷して終わった明細には、どちらも出さない。
明細の表の残の枡には「完了」か「完了 (出さない n)」の印、盤の判断の印には「完了」(足りないまま完了にした明細だけ) を出す。
作り物のデータだけを使う。
"""

import json
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import OrderLine
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
DAY = date(2026, 11, 5)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 書く権限だけを効かせる (金額は見られる。tests/test_orders_no_bill_clear.py と同じ)
NO_WRITE = {"department": "off", "write": "on", "money": "off"}
#: 画面に出さない字 (依頼書の 9)
BANNED = ("打ち切", "開き直")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": f"T-{n}00", "name": f"作り物の部品 {n}", "unit": "個"}, user)
        for n in range(1, 4)
    ]


def _order(user, customer, items, qtys):
    order = services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")} for item, qty in zip(items, qtys, strict=False)],
        user,
        order_date=ORDER_DATE,
    )
    return order, list(order.lines.order_by("line_no"))


@pytest.fixture
def short_line(user, customer, items):
    """数量 5 で 3 を出荷した明細 (残 2)。"""
    _order_obj, (line,) = _order(user, customer, items, ["5"])
    shipping_services.ship(line, Decimal("3"), DAY, user)
    return _line(line.pk)


def _line(pk):
    return OrderLine.objects.get(pk=pk)


def _urls(line):
    return reverse("orders:orderline_complete", args=[line.pk]), reverse("orders:orderline_complete_clear", args=[line.pk])


def _has_link(body, url):
    """⋮ の項目の link (画面へ移る href と、小窓で開く data-modal-form) があるか。

    完了を外すの URL は完了にするの URL を頭に含むので、URL の全体を属性の形で見る。
    """
    return f'href="{url}?next=' in body and f'data-modal-form="{url}"' in body


def _detail(client, line):
    return client.get(reverse("orders:order_detail", args=[line.order_id])).content.decode()


def _detail_rows(body):
    """受注の詳細の明細の表の行 (<tr class="h-10" data-row-menu> ... </tr>) の HTML の並び。"""
    return re.findall(r'<tr class="h-10" data-row-menu>.*?</tr>', body, re.S)


def _board_row(body, pk):
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


# ---------------------------------------------------------------- (a) 受注の詳細の明細の ⋮


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
def test_no_complete_item_for_cancelled_order(client, user, short_line):
    services.cancel_order(short_line.order, user)
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)

    body = _detail(client, short_line)
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
def test_line_shipped_to_qty_has_neither_item(client, user, short_line):
    shipping_services.ship(short_line, Decimal("2"), DAY, user)
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)

    body = _detail(client, short_line)
    assert not _has_link(body, complete_url)
    assert not _has_link(body, clear_url)


# ---------------------------------------------------------------- (b) 完了にするの確かめの画面


@pytest.mark.django_db
def test_complete_confirm_screen_and_save(client, user, short_line):
    client.force_login(user)
    complete_url, _clear_url = _urls(short_line)
    label = f"{short_line.order.order_no}-1"
    back = reverse("orders:order_detail", args=[short_line.order_id])

    for page in (client.get(complete_url), client.get(complete_url, **MODAL)):
        html = page.content.decode()
        assert page.status_code == 200
        assert "残り 2 は出さずに終えます。出した数と金額はそのまま残り、請求の候補にも残ります。" in html
        assert f"{label} を完了にする" in html
        assert "btn btn-error" not in html  # 取消でなく外せるので赤にしない

    modal = client.post(complete_url, {}, **MODAL)
    assert modal.status_code == 204
    saved = json.loads(modal["HX-Trigger"])["seizou:saved"]
    assert saved["message"] == (
        f"{label} を完了にしました。残り 2 は出さずに終えます。また出すときは、明細の ⋮ から完了を外してください。"
    )
    assert saved["level"] == "success"
    line = _line(short_line.pk)
    assert line.completed is True
    assert services.remaining_qty(line) == Decimal("0")
    assert services.unfilled_qty(line) == Decimal("2")

    # JS の無いときは画面の全体から、開いた元の画面 (?next=) へ戻る
    services.clear_completed(line, user)
    response = client.post(complete_url, {"next": back})
    assert response.status_code == 302
    assert response["Location"] == back
    assert _line(short_line.pk).completed is True


# ---------------------------------------------------------------- (c) 完了を外す


@pytest.mark.django_db
def test_clear_confirm_screen_restores_remaining(client, user, short_line):
    services.mark_completed(short_line, user)
    client.force_login(user)
    _complete_url, clear_url = _urls(short_line)
    label = f"{short_line.order.order_no}-1"

    page = client.get(clear_url, **MODAL).content.decode()
    assert f"{label} の完了を外す" in page
    assert "btn btn-error" not in page

    modal = client.post(clear_url, {}, **MODAL)
    assert modal.status_code == 204
    assert json.loads(modal["HX-Trigger"])["seizou:saved"]["message"] == f"{label} の完了を外しました。残り 2 をまた出せます。"
    line = _line(short_line.pk)
    assert line.completed is False
    assert services.remaining_qty(line) == Decimal("2")


# ---------------------------------------------------------------- (d) 明細の表の残の枡の印


@pytest.mark.django_db
def test_remaining_cell_marks(client, user, customer, items):
    _order_obj, (short, filled, shipped) = _order(user, customer, items, ["5", "5", "5"])
    shipping_services.ship(short, Decimal("3"), DAY, user)
    services.mark_completed(short, user)
    # 完了にした後に数量まで出荷した明細
    shipping_services.ship(filled, Decimal("1"), DAY, user)
    services.mark_completed(filled, user)
    shipping_services.ship(_line(filled.pk), Decimal("4"), DAY, user)
    # 数量まで出荷しただけの明細 (印は偽)
    shipping_services.ship(shipped, Decimal("5"), DAY, user)
    client.force_login(user)

    rows = _detail_rows(_detail(client, short))
    assert len(rows) == 3
    assert '<span class="badge badge-soft badge-ghost">完了 (出さない 2)</span>' in rows[0]
    assert '<span class="badge badge-soft badge-ghost">完了</span>' in rows[1]
    assert ">完了" not in rows[2]


# ---------------------------------------------------------------- (e) 生産管理ボード


@pytest.mark.django_db
def test_board_hides_completed_line_and_marks_it(client, user, customer, items):
    _order_obj, (short, filled, open_line) = _order(user, customer, items, ["5", "5", "5"])
    shipping_services.ship(short, Decimal("3"), DAY, user)
    services.mark_completed(short, user)
    shipping_services.ship(filled, Decimal("1"), DAY, user)
    services.mark_completed(filled, user)
    shipping_services.ship(_line(filled.pk), Decimal("4"), DAY, user)
    client.force_login(user)
    board_url = reverse("orders:orderline_list")

    default = client.get(board_url).content.decode()
    assert f'id="row-{short.pk}"' not in default
    # 終わっていない明細の行の ⋮ には「完了にする」がある
    complete_url, clear_url = _urls(open_line)
    open_row = _board_row(default, open_line.pk)
    assert _has_link(open_row, complete_url)
    assert "完了にする" in open_row

    everything = client.get(board_url, {"f_open": "0"}).content.decode()
    assert '<span class="badge badge-ghost text-base-content/60">完了</span>' in _board_row(everything, short.pk)
    # 数量に届いた完了の明細の印は「出荷済み」のまま
    assert '<span class="badge badge-ghost text-base-content/60">出荷済み</span>' in _board_row(everything, filled.pk)
    # 完了の明細の行には、完了にするも完了を外すも出さない (盤の既定の一覧に完了の明細は出ないので、外すは受注の詳細から)
    for line in (short, filled):
        row = _board_row(everything, line.pk)
        for url in _urls(line):
            assert not _has_link(row, url)


@pytest.mark.django_db
def test_board_complete_item_follows_write_permission(settings, client, user, short_line):
    client.force_login(user)
    complete_url, _clear_url = _urls(short_line)
    board_url = reverse("orders:orderline_list")
    assert _has_link(_board_row(client.get(board_url).content.decode(), short_line.pk), complete_url)

    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    assert not _has_link(_board_row(client.get(board_url).content.decode(), short_line.pk), complete_url)


# ---------------------------------------------------------------- (f) 出荷の新規登録の「残りは出さずに完了にする」


@pytest.mark.django_db
def test_shipment_close_rest_completes_line(client, user, short_line):
    client.force_login(user)
    url = reverse("shipping:shipment_create")
    label = f"{short_line.order.order_no}-1"

    page = client.get(url, {"order_line": short_line.pk}).content.decode()
    assert "残りは出さずに完了にする" in page
    assert "この出荷の後の残りを出さずに、明細を完了にします。" in page

    modal = client.post(
        url,
        {"order_line": short_line.pk, "qty": "1", "shipped_at": DAY.isoformat(), "close_rest": "on"},
        **MODAL,
    )
    assert modal.status_code == 204
    assert json.loads(modal["HX-Trigger"])["seizou:saved"]["message"] == (
        f"出荷を保存しました。{label} を完了にしました。残り 1 は出さずに終えます。また出すときは、明細の ⋮ から完了を外してください。"
    )
    line = _line(short_line.pk)
    assert line.completed is True
    assert services.unfilled_qty(line) == Decimal("1")
    assert services.remaining_qty(line) == Decimal("0")


@pytest.mark.django_db
def test_shipment_without_close_rest_is_unchanged(client, user, short_line):
    client.force_login(user)
    url = reverse("shipping:shipment_create")

    modal = client.post(url, {"order_line": short_line.pk, "qty": "1", "shipped_at": DAY.isoformat()}, **MODAL)
    assert modal.status_code == 204
    assert "完了" not in json.loads(modal["HX-Trigger"])["seizou:saved"]["message"]
    line = _line(short_line.pk)
    assert line.completed is False
    assert services.remaining_qty(line) == Decimal("1")


# ---------------------------------------------------------------- (g) 権限


@pytest.mark.django_db
def test_writer_only_can_complete_and_clear(settings, client, user, short_line):
    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)

    # 開いた時点で断る (「この画面は開けません」。保存の時と同じ訳の字。本人の決め 11-12 07:2x)。直に送っても、土台の権限の断り (赤) になる
    opened = client.get(complete_url)
    assert opened.status_code == 403 and "受注の編集の権限がありません。" in opened.content.decode()
    refused = client.post(complete_url, {}).content.decode()
    assert "受注の編集の権限がありません。" in refused
    assert _line(short_line.pk).completed is False

    services.mark_completed(short_line, user)
    refused = client.post(clear_url, {}).content.decode()
    assert "受注の編集の権限がありません。" in refused
    assert _line(short_line.pk).completed is True


# ---------------------------------------------------------------- (h) 画面の字


@pytest.mark.django_db
def test_screens_do_not_use_banned_words(client, user, short_line):
    client.force_login(user)
    complete_url, clear_url = _urls(short_line)
    pages = [
        _detail(client, short_line),
        client.get(complete_url).content.decode(),
        client.get(reverse("orders:orderline_list")).content.decode(),
        client.get(reverse("shipping:shipment_create"), {"order_line": short_line.pk}).content.decode(),
    ]
    services.mark_completed(short_line, user)
    pages += [_detail(client, short_line), client.get(clear_url).content.decode()]
    for html in pages:
        for word in BANNED:
            assert word not in html
