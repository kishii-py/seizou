"""注文残の盤の小窓と、選んだ行の一括の出荷と発注 (決め 11-04 17:4x・17:5x〜18:0x。N124)。

- 左のメニューの受注管理の先頭に「注文残」の入口がある。
- 盤の行の頭 (受注番号の枡の中) の選ぶ欄と、表の上の「選んだ行の操作」の帯。出荷と発注の権限が無い人には出さない。
- 行のメニューの出荷・指示書・実績・発注は data-modal-form を持ち、入力の土台の小窓で開く (href はそのまま)。
- 一括出荷の入力 (shipping:shipment_bulk_create) は、選んだ明細を 1 回の保存で出荷する。どれか 1 行が R01 か services の
  決まりで止まると全部を保存しない。出荷の知らせは全部を保存した後に atomic の外で送る (R17)。
- 発注の入力は、選んだ明細 (?line= の繰り返し) を明細の行の初期値に埋める。
- 行の押下で選ぶ (決め 11-04 22:4x。N137): 選ぶ欄は見せない class で、見出しの全部を選ぶ欄は無く、帯に「全部を選ぶ」「選択を外す」。
作り物のデータだけを使う。作り物の作り方は tests/test_orders_board.py と tests/test_shipping_ship.py を写した。
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.core.context_processors import core_menu, menu, sections
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from seizou.workorders import services as workorders_services
from tests.test_shipping_notice import _inbox_order

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
JS = Path(orders_services.__file__).resolve().parent / "static" / "orders" / "board-select.js"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)


def _line(user, customer, item, *, qty="10", due=None):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": due}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def lines(user, customer, item, plate):
    """a = 残 10、b = 10 のうち 4 を出荷した残 6、c = 取消の受注の明細。"""
    a = _line(user, customer, item, due=timezone.localdate() + timedelta(days=3))
    b = _line(user, customer, plate, due=timezone.localdate() + timedelta(days=5))
    shipping_services.ship(b, Decimal("4"), ORDER_DATE, user)
    c = _line(user, customer, item)
    orders_services.cancel_order(c.order, user)
    return a, b, c


def _tr(body, pk):
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def _band(body):
    match = re.search(r'<form id="board-bulk".*?</form>', body, re.S)
    assert match, "選んだ行の操作の帯が無い"
    return match.group(0)


def _post(*picked, **extra):
    """一括出荷の送る値。picked は (明細, 数量) の組。"""
    data = {"line": [str(line.pk) for line, _qty in picked], "shipped_at": SHIPPED_AT.isoformat(), "note": "作り物の備考"}
    for line, qty in picked:
        data[f"qty_{line.pk}"] = str(qty)
    data.update(extra)
    return data


def _bulk_url(*picked):
    return reverse("shipping:shipment_bulk_create") + "?" + "&".join(f"line={line.pk}" for line in picked)


# --- 1. 左のメニュー ---


@pytest.mark.django_db
def test_menu_has_board_entry_first_in_orders_group():
    groups = sections([*core_menu(), *menu()])
    production = next(group for group in groups if group["label"] == "生産管理部")
    orders_group = next(group for group in production["groups"] if group["label"] == "受注管理")
    assert [entry["label"] for entry in orders_group["items"]] == ["注文残", "受注", "届いた注文"]


# --- 2〜4. 盤の選ぶ欄と帯 ---


@pytest.mark.django_db
def test_board_has_pick_boxes_and_band(logged_in, lines):
    a, b, _c = lines
    body = logged_in.get(reverse("orders:orderline_list")).content.decode()

    for line in (a, b):
        tr_html = _tr(body, line.pk)
        assert f'name="line" value="{line.pk}" form="board-bulk"' in tr_html
        # 選ぶ欄は行の頭 (受注番号の枡の中。列は足さない)。
        first_td = re.search(r"<td[^>]*>.*?</td>", tr_html, re.S).group(0)
        assert "data-board-pick" in first_td and line.order.order_no in first_td
        # 行の押下で選ぶ (N137): 欄は見せない class で、欄と受注番号の link の間に字を置かない (受注番号の字が行ごとにずれない)。
        assert '<input type="checkbox" class="sr-only" name="line"' in first_td
        assert 'data-board-pick><a class="link' in first_td
        assert "checkbox checkbox-sm" not in tr_html
        # 選んだ行の地は、JS が付ける data-tone と行の固定の class で決まる (R19)。
        assert tr_html.startswith(f'<tr id="row-{line.pk}" class="row-hover h-10 data-[tone=primary]:bg-primary/15" data-row-menu>')
    thead = re.search(r"<thead>.*?</thead>", body, re.S).group(0)
    assert 'type="checkbox"' not in thead and "data-board-all" not in thead
    assert body.count('id="board-bulk"') == 1
    band = _band(body)
    assert re.match(r'<form id="board-bulk"[^>]* hidden data-board-bulk>', band)
    assert f'hx-get="{reverse("shipping:shipment_bulk_create")}"' in band
    assert f'hx-get="{reverse("purchasing:purchaseorder_create")}"' in band
    assert band.count('hx-target="#seizou-modal-box"') == 2
    assert band.count('hx-include="#board-bulk"') == 2
    assert 'type="submit"' not in band
    assert '<button type="button" class="link link-hover text-sm" data-board-all>全部を選ぶ</button>' in band
    assert '<button type="button" class="link link-hover text-sm" data-board-none>選択を外す</button>' in band
    assert body.count("data-board-all") == 1
    assert "orders/board-select.js" in body


@pytest.mark.django_db
def test_cancelled_and_finished_rows_have_no_pick_box(user, logged_in, customer, item, lines):
    a, _b, cancelled = lines
    done = _line(user, customer, item, qty="2")
    shipping_services.ship(done, Decimal("2"), ORDER_DATE, user)

    body = logged_in.get(reverse("orders:orderline_list"), {"f_open": "0"}).content.decode()  # N126 で「すべて」は印 f_open=0

    for line in (cancelled, done):
        tr_html = _tr(body, line.pk)
        assert "data-board-pick" not in tr_html
        assert 'name="line"' not in tr_html
    assert "data-board-pick" in _tr(body, a.pk)


@pytest.mark.django_db
def test_no_write_permission_hides_band_and_pick_boxes(settings, client, user, lines):
    a, _b, _c = lines
    settings.SEIZOU = {"access_control": {"department": "log", "write": "on", "money": "log"}}
    client.force_login(user)

    body = client.get(reverse("orders:orderline_list")).content.decode()

    assert 'id="board-bulk"' not in body
    assert "data-board-pick" not in body
    assert "data-board-all" not in body
    assert "出荷する" not in _tr(body, a.pk)

    admin = get_user_model().objects.create_superuser("admin-tester", password="pass-for-tests-only")
    client.force_login(admin)
    assert 'id="board-bulk"' in client.get(reverse("orders:orderline_list")).content.decode()


def test_board_select_js_picks_rows_by_press():
    """行の押下で選ぶ JS の約束 (N137)。ブラウザで動かさず、字で見る (ブラウザでの確かめは本体の手の確かめ)。"""
    text = JS.read_text(encoding="utf-8")
    # 行の押下を document に委譲し、⋮ のメニューを閉じる押下 (core の js/seizou-row-menu.js) より先に見る (capture)。
    assert 'document.addEventListener("click", onClick, true)' in text
    for word in (
        '"tr[data-row-menu]"',
        '"[data-board-pick]"',
        '"a, button, input, select, textarea, label, summary, details"',
        '"details.row-menu[open]"',
        'row.dataset.tone = "primary"',
        "delete row.dataset.tone",
        'new Event("change", { bubbles: true })',
        "event.shiftKey",
        "[data-board-all]",
        "[data-board-none]",
        "htmx:afterSettle",
        "band.hidden = picked === 0",
    ):
        assert word in text, word
    # 選ぶ欄の無い行 (取消の受注と残の無い明細) は、押しても選ばず地も変えない。
    assert "if (!box) return;" in text
    # 右クリックのメニューには触らない。見せない class と class の字を触らない (R19)。取った字を HTML にしない。通信しない。
    for word in ("contextmenu", "sr-only", "classList", "className", "innerHTML", "fetch(", "window.htmx"):
        assert word not in text, word


# --- 5. 行のメニューの小窓の印 ---


@pytest.mark.django_db
def test_row_menus_open_forms_in_modal(user, logged_in, customer, item, lines):
    a, _b, _c = lines
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    bend = masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)
    workorder = workorders_services.issue_workorder(
        a, Decimal("10"), [{"process": p, "planned_qty": Decimal("10")} for p in (cut, bend)], user
    )
    first = workorder.steps.get(seq=1)
    progress_services.record_progress(first, Decimal("3"), timezone.now(), user)
    urls = {
        "ship": f'{reverse("shipping:shipment_create")}?order_line={a.pk}',
        "issue": f'{reverse("workorders:workorder_create")}?order_line={a.pk}',
        "progress": f'{reverse("progress:progress_create")}?workorder_step={first.pk}',
        "po": f'{reverse("purchasing:purchaseorder_create")}?from_line={a.pk}',
    }

    tr_html = _tr(logged_in.get(reverse("orders:orderline_list")).content.decode(), a.pk)
    for url in urls.values():
        assert f'href="{url}" data-modal-form="{url}"' in tr_html, url
    # 受注の詳細・部品の小窓は今のまま (小窓の印を付けない)。
    assert f'href="{reverse("orders:order_detail", args=[a.order_id])}" data-modal-form' not in tr_html
    assert tr_html.count("data-modal-form=") == 4

    detail = logged_in.get(reverse("orders:order_detail", args=[a.order_id])).content.decode()
    for name in ("ship", "issue", "po"):
        assert f'href="{urls[name]}" data-modal-form="{urls[name]}"' in detail, name
    assert detail.count("data-modal-form=") == 3


# --- 6〜11. 一括出荷 ---


@pytest.mark.django_db
def test_bulk_get_lists_picked_rows_full_and_modal(logged_in, lines):
    a, b, c = lines
    url = _bulk_url(a, b, c)

    response = logged_in.get(url)
    body = response.content.decode()
    form = response.context["form"]
    assert [row["line"].pk for row in form.rows] == [a.pk, b.pk]
    assert [row["qty"].value() for row in form.rows] == [Decimal("10"), Decimal("6")]
    assert form.dropped == 1
    assert "1 行は出していません" in body
    assert "<html" in body
    for line in (a, b):
        assert f'<input type="hidden" name="line" value="{line.pk}">' in body
    assert f'<input type="hidden" name="line" value="{c.pk}">' not in body

    modal = logged_in.get(url, **MODAL).content.decode()
    assert "<html" not in modal
    assert "hx-post=" in modal
    assert "一括出荷" in modal
    # 小窓でも明細の表のカード (shipping/_form_modal.html の出し分け) が出る。
    for line in (a, b):
        assert f'<input type="hidden" name="line" value="{line.pk}">' in modal
        assert f'name="qty_{line.pk}"' in modal


@pytest.mark.django_db
def test_bulk_post_full_page_creates_shipments_and_redirects(logged_in, lines):
    a, b, _c = lines
    before = Shipment.objects.count()

    response = logged_in.post(_bulk_url(a, b), _post((a, 10), (b, 6)))

    assert response.status_code == 302
    assert response["Location"] == reverse("shipping:shipment_list")
    assert Shipment.objects.count() == before + 2
    assert Shipment.objects.filter(order_line=a, qty=Decimal("10"), shipped_at=SHIPPED_AT, note="作り物の備考").exists()
    assert Shipment.objects.filter(order_line=b, qty=Decimal("6")).exists()
    assert [message.message for message in get_messages(response.wsgi_request)] == ["2 件の出荷を登録しました。"]


@pytest.mark.django_db
def test_bulk_post_in_modal_returns_204_with_trigger(logged_in, lines):
    a, b, _c = lines
    before = Shipment.objects.count()

    response = logged_in.post(_bulk_url(a, b), _post((a, 10), (b, 6)), **MODAL)

    assert response.status_code == 204
    saved = json.loads(response["HX-Trigger"])["seizou:saved"]
    assert saved == {"message": "2 件の出荷を登録しました。", "level": "success"}
    assert Shipment.objects.count() == before + 2
    assert list(get_messages(response.wsgi_request)) == []


@pytest.mark.django_db
def test_r01_stops_or_warns_for_whole_bulk(settings, logged_in, lines):
    a, b, _c = lines
    before = Shipment.objects.count()

    settings.SEIZOU = {"over_shipment": "stop"}
    body = logged_in.post(_bulk_url(a, b), _post((a, 10), (b, 7))).content.decode()
    assert Shipment.objects.count() == before
    assert f"{b.order.order_no} の {b.line_no} 行目: 出荷の数の合計 (11) が受注の数 (10) を超えます。" in body
    assert f"{a.order.order_no} の {a.line_no} 行目:" not in body

    settings.SEIZOU = {"over_shipment": "warn"}
    first = logged_in.post(_bulk_url(a, b), _post((a, 10), (b, 7)))
    assert first.status_code == 200
    assert '<input type="hidden" name="confirmed" value="1">' in first.content.decode()
    assert "このまま保存" in first.content.decode()
    assert Shipment.objects.count() == before
    second = logged_in.post(_bulk_url(a, b), _post((a, 10), (b, 7), confirmed="1"))
    assert second.status_code == 302
    assert Shipment.objects.count() == before + 2


@pytest.mark.django_db
def test_services_rule_rolls_back_every_row(user, logged_in, customer, item, lines):
    a, _b, _c = lines
    d = _line(user, customer, item, qty="5")
    assert logged_in.get(_bulk_url(a, d)).status_code == 200
    # 開いた後に d の受注が取消になった。送った行はそのまま services に渡り、R05 で全部を巻き戻す。
    orders_services.cancel_order(d.order, user)
    before = Shipment.objects.count()

    response = logged_in.post(_bulk_url(a, d), _post((a, 10), (d, 5)))

    assert response.status_code == 200
    body = response.content.decode()
    assert f"{d.order.order_no} の {d.line_no} 行目: 取消した受注には、出荷も指示書も作れない。" in body
    assert Shipment.objects.count() == before
    assert not Shipment.objects.filter(order_line=a).exists()


@pytest.mark.django_db
def test_skipped_rows_are_not_shipped(logged_in, lines):
    a, b, _c = lines
    before = Shipment.objects.count()

    response = logged_in.post(_bulk_url(a, b), _post((a, 10), (b, 6), **{f"skip_{b.pk}": "on"}))
    assert response.status_code == 302
    assert Shipment.objects.count() == before + 1
    assert Shipment.objects.filter(order_line=a).exists()

    every = _post((a, 10), (b, 6), **{f"skip_{a.pk}": "on", f"skip_{b.pk}": "on"})
    body = logged_in.post(_bulk_url(a, b), every).content.decode()
    assert "出荷する明細がありません。" in body
    assert Shipment.objects.count() == before + 1


# --- 12. 発注の入力 ---


@pytest.mark.django_db
def test_purchase_order_form_prefills_picked_lines(logged_in, lines):
    a, b, c = lines
    url = reverse("purchasing:purchaseorder_create")

    response = logged_in.get(f"{url}?line={a.pk}&line={b.pk}")
    initial = response.context["form"].lines.initial
    assert [row["item"] for row in initial] == [a.item_id, b.item_id]
    assert [row["qty"] for row in initial] == [Decimal("10"), Decimal("6")]
    assert [row["due_date"] for row in initial] == [a.due_date, b.due_date]
    body = response.content.decode()
    assert 'name="lines-0-item"' in body and 'name="lines-1-qty"' in body

    only_open = logged_in.get(f"{url}?line={a.pk}&line={c.pk}")
    assert [row["item"] for row in only_open.context["form"].lines.initial] == [a.item_id]

    # 小窓でも明細の行の表と「行を足す」が出る (purchasing/_form_modal.html の出し分け)。
    modal = logged_in.get(f"{url}?line={a.pk}&line={b.pk}", **MODAL).content.decode()
    assert "<html" not in modal
    assert "hx-post=" in modal
    assert 'name="lines-1-qty"' in modal and "data-add-line" in modal


# --- 13. 出荷の知らせは atomic の外 (R17) ---


@pytest.mark.django_db
def test_bulk_sends_notices_outside_atomic(monkeypatch, user, customer, item):
    first = _inbox_order(customer, item, user).lines.get()
    second = _inbox_order(customer, item, user).lines.get()
    depths = []

    def fake_send(shipment, by):
        depths.append(len(connection.atomic_blocks))
        return shipment

    monkeypatch.setattr(connect_services, "send_shipping_notice", fake_send)
    outside = len(connection.atomic_blocks)

    shipped = shipping_services.ship_lines([(first, Decimal("3")), (second, Decimal("4"))], SHIPPED_AT, user)

    assert depths == [outside, outside]
    assert [shipment.order_line_id for shipment in shipped] == [first.pk, second.pk]
    assert shipped.saved_message == "2 件の出荷を登録しました。"
