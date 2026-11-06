"""詳細の頭の型 (決め 11-05 16:2x。N163)。

1 段目にパンくず (一覧の名前 › 題。一覧の名前は土台の BaseDetailView が一覧の画面の screen_name から引く)、
2 段目の左に大きい題・状態の印・「?」、右にボタン (副 → 「⋯」→ 主 1 つ)。赤い操作 (取消・削除) は「⋯」のメニューの最後。
パンくずがあれば「一覧に戻る」は出さない。作り物のデータは全部 services で作る。
"""

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

import seizou.core
import seizou.masters.views
from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.core.templatetags.seizou_tags import comma
from seizou.manuals import services as manuals_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services
from tests import test_detail_columns
from tests.test_r10_inbox_accept import _line, _payload

# 受注と発注の詳細の 2 列の試験の作り物を借りる (pytest は module の名前で fixture を見つける)。
customer = test_detail_columns.customer
subcontractor = test_detail_columns.subcontractor
item = test_detail_columns.item
order = test_detail_columns.order
purchase_order = test_detail_columns.purchase_order

#: 編集の判定を効かせる (Access の行の無い利用者は書けない。見るのはできる)
ALL_ON = {"department": "on", "write": "on", "money": "on"}
MORE_MENU = '<details class="dropdown dropdown-end row-menu" data-detail-more>'


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def workorder(user, order, cut):
    """発行 (未着手) の指示書。受注の 1 行目から 10 個。"""
    line = order.lines.order_by("line_no").first()
    return workorders_services.issue_workorder(line, Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user)


@pytest.fixture
def shipment(user, order):
    line = order.lines.order_by("line_no").first()
    return shipping_services.ship(line, Decimal("10"), timezone.localdate(), user)


@pytest.fixture
def delivery_note(user, customer, shipment):
    return shipping_services.create_delivery_note(customer, [shipment], user)


@pytest.fixture
def invoice(user, customer, shipment):
    return billing_services.create_invoice(customer, [shipment], user, issued_on=timezone.localdate())


@pytest.fixture
def inbox(user, customer):
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    return connect_services.record_inbox(customer, payload, user, received_at=timezone.now() - timedelta(minutes=1))


@pytest.fixture
def holiday(user):
    return masters_services.create_holiday({"date": date(2026, 12, 29), "name": "作り物の会社の休み", "kind": "company"}, user)


def _get(client, name, pk):
    response = client.get(reverse(name, args=[pk]))
    assert response.status_code == 200, name
    return response, response.content.decode()


def _crumb(body):
    return re.search(r'<nav aria-label="パンくず"[^>]*>.*?</nav>', body, re.S).group(0)


def _actions(body):
    """頭の右のボタンの箱 (中に div は無い)。"""
    start = body.index("data-detail-actions")
    return body[start : body.index("</div>", start)]


def _more(body):
    start = body.index(MORE_MENU)
    return body[start : body.index("</details>", start)]


@pytest.mark.django_db
def test_crumb_shows_list_name_on_every_detail(
    logged_in, item, customer, order, purchase_order, workorder, shipment, delivery_note, invoice, inbox
):
    cases = (
        ("masters:item_detail", item, "masters:item_list", "部品マスター"),
        ("masters:partner_detail", customer, "masters:partner_list", "取引先"),
        ("orders:order_detail", order, "orders:order_list", "受注"),
        ("purchasing:purchaseorder_detail", purchase_order, "purchasing:purchaseorder_list", "発注"),
        ("workorders:workorder_detail", workorder, "workorders:workorder_list", "指示書"),
        ("shipping:shipment_detail", shipment, "shipping:shipment_list", "出荷"),
        ("shipping:deliverynote_detail", delivery_note, "shipping:deliverynote_list", "納品書"),
        ("billing:invoice_detail", invoice, "billing:invoice_list", "請求書"),
        ("connect:inbox_detail", inbox, "connect:inbox_list", "受信した注文"),
    )
    for name, obj, list_url_name, list_name in cases:
        response, body = _get(logged_in, name, obj.pk)
        crumb = _crumb(body)
        assert f'href="{reverse(list_url_name)}">{list_name}</a>' in crumb, name
        assert f'aria-current="page">{escape(str(obj))}</span>' in crumb, name
        assert body.index('<nav aria-label="パンくず"') < body.index('<h1 class="truncate'), name
        assert "一覧に戻る" not in body, name
    response = _get(logged_in, "masters:item_detail", item.pk)[0]
    assert response.context["list_name"] == "部品マスター"


@pytest.mark.django_db
def test_back_to_list_only_when_no_list_name(logged_in, item, monkeypatch):
    monkeypatch.setattr(seizou.masters.views.ItemListView, "screen_name", "")

    response, body = _get(logged_in, "masters:item_detail", item.pk)

    assert response.context["list_name"] == ""
    assert '<nav aria-label="パンくず"' not in body
    actions = _actions(body)
    assert "一覧に戻る" in actions
    assert f'href="{reverse("masters:item_list")}"' in actions


@pytest.mark.django_db
def test_head_order_and_one_primary(logged_in, item, order, workorder, purchase_order):
    body = _get(logged_in, "orders:order_detail", order.pk)[1]
    # 明細 2 行とも残がある受注は、主のボタンが緑の「出荷登録」(行を選ぶメニュー) で、「編集」は白 (C15-1 b・UX-2 a)
    edit = f'<a class="btn btn-outline border-base-300" href="{reverse("orders:order_update", args=[order.pk])}?next={reverse("orders:order_detail", args=[order.pk])}">'
    marks = ('<nav aria-label="パンくず"', '<h1 class="truncate', 'aria-label="この画面の説明"', "data-detail-actions")
    positions = [body.index(mark) for mark in marks]
    start = body.index("data-detail-actions")
    positions += [body.index(edit, start), body.index("data-detail-more", start), body.index('<summary class="btn btn-primary">', start)]
    assert positions == sorted(positions)
    assert body[start : body.index('id="order-stages"')].count("btn-primary") == 1

    actions = _actions(_get(logged_in, "workorders:workorder_detail", workorder.pk)[1])
    assert actions.index("現品票</a>") < actions.index("data-detail-more") < actions.index("着手</button>")
    assert actions.count("btn-primary") == 1

    actions = _actions(_get(logged_in, "purchasing:purchaseorder_detail", purchase_order.pk)[1])
    assert actions.index("data-detail-more") < actions.index("再送</button>")
    assert actions.count("btn-primary") == 1

    actions = _actions(_get(logged_in, "masters:item_detail", item.pk)[1])
    assert actions.count("<a ") == 1 and "編集</a>" in actions
    assert "data-detail-more" not in actions and "<form" not in actions


def _ship_link(line):
    """明細 1 行の出荷の小窓の入口 (明細の ⋮ の「出荷する」と同じ href と data-modal-form)。"""
    url = f'{reverse("shipping:shipment_create")}?order_line={line.pk}'
    return f'href="{url}" data-modal-form="{url}"'


@pytest.mark.django_db
def test_order_head_ship_menu_for_open_lines(logged_in, order):
    """候補 C15 の 4 (C15-1 b・UX-2 a): 残のある明細が 2 行の受注は、頭の右端に緑の「出荷登録」のメニュー (行番号・品番・残) を出し、
    「編集」は白で「⋯」の左。"""
    actions = _actions(_get(logged_in, "orders:order_detail", order.pk)[1])
    lines = list(orders_services.annotate_remaining(order.lines.all()).order_by("line_no"))

    assert '<summary class="btn btn-primary">' in actions and "出荷登録" in actions
    assert len(lines) == 2
    for line in lines:
        assert actions.count(_ship_link(line)) == 1, line.line_no
        assert f"残 {comma(line.remaining_total)}" in actions, line.line_no
    edit = f'<a class="btn btn-outline border-base-300" href="{reverse("orders:order_update", args=[order.pk])}'
    assert actions.index(edit) < actions.index("data-detail-more") < actions.index('<summary class="btn btn-primary">')
    assert actions.count("btn-primary") == 1


@pytest.mark.django_db
def test_order_head_ship_link_for_one_open_line(logged_in, order, shipment):
    """残のある明細が 1 行 (1 行目を出し切った) の受注は、緑の「出荷登録」がその行の出荷の小窓を直に開く。"""
    first, second = order.lines.order_by("line_no")
    actions = _actions(_get(logged_in, "orders:order_detail", order.pk)[1])

    assert f'<a class="btn btn-primary" {_ship_link(second)}>' in actions
    assert f'?order_line={first.pk}"' not in actions
    assert '<summary class="btn btn-primary">' not in actions


@pytest.mark.django_db
def test_order_head_no_ship_when_done_or_cancelled(user, logged_in, customer, item, order, shipment):
    """全部の明細を出し切った受注と取消の受注には「出荷登録」を出さない。出し切った受注の「編集」は今の緑、取消の受注は「編集」も無い。"""
    shipping_services.ship(order.lines.get(line_no=2), Decimal("2"), timezone.localdate(), user)
    actions = _actions(_get(logged_in, "orders:order_detail", order.pk)[1])
    assert "出荷登録" not in actions and "?order_line=" not in actions
    assert f'<a class="btn btn-primary" href="{reverse("orders:order_update", args=[order.pk])}' in actions

    # 残のある明細を持つ受注を取り消す (出し切った受注の取消では、残の条件だけで通ってしまうため)
    lines = [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("1")}]
    cancelled = orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))
    orders_services.cancel_order(cancelled, user)
    actions = _actions(_get(logged_in, "orders:order_detail", cancelled.pk)[1])
    assert "出荷登録" not in actions and "?order_line=" not in actions
    assert f'href="{reverse("orders:order_update", args=[cancelled.pk])}' not in actions


@pytest.mark.django_db
def test_red_actions_live_in_more_menu(logged_in, order, purchase_order, workorder, delivery_note, invoice, holiday):
    cases = (
        ("orders:order_detail", order, "orders:order_cancel", "取消", True),
        ("purchasing:purchaseorder_detail", purchase_order, "purchasing:purchaseorder_cancel", "取消", True),
        ("workorders:workorder_detail", workorder, "workorders:workorder_cancel", "取消", True),
        ("shipping:deliverynote_detail", delivery_note, "shipping:deliverynote_cancel", "取消", True),
        ("billing:invoice_detail", invoice, "billing:invoice_cancel", "取消", True),
        ("masters:holiday_detail", holiday, "masters:holiday_delete", "削除", False),
    )
    for name, obj, action, word, modal in cases:
        body = _get(logged_in, name, obj.pk)[1]
        url = reverse(action, args=[obj.pk])
        menu = _more(body)
        last = menu.rsplit("<li>", 1)[1]
        assert f'href="{url}"' in last and "text-error" in last and f"{word}</a>" in last, name
        assert "btn-error" not in body, name
        assert (f'href="{url}" data-modal-form="{url}"' in menu) is modal, name
        if not modal:
            assert "data-modal-form" not in menu, name
        # 赤い操作はボタンの列に出さない (「⋯」の外に同じ URL の link が無い)
        assert body.count(f'href="{url}"') == 1, name


@pytest.mark.django_db
def test_no_more_menu_without_red_action(client, settings, user, customer, item, order):
    client.force_login(user)
    body = _get(client, "masters:item_detail", item.pk)[1]
    assert "data-detail-more" not in body

    lines = [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("1")}]
    cancelled = orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))
    orders_services.cancel_order(cancelled, user)
    body = _get(client, "orders:order_detail", cancelled.pk)[1]
    assert "data-detail-more" not in body

    # 書く権限の無い人 (編集の判定を効かせた設定の、Access の行の無い利用者) は「⋯」も取消も無い
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    body = _get(client, "orders:order_detail", order.pk)[1]
    assert "data-detail-more" not in body
    assert f'href="{reverse("orders:order_cancel", args=[order.pk])}"' not in body


@pytest.mark.django_db
def test_manual_detail_rare_actions_in_more(client, settings, user):
    manual = manuals_services.create_manual(None, "作り物の手順", "steps", user)
    pk = manual.pk
    move, protect, delete = (reverse(f"manuals:{name}", args=[pk]) for name in ("manual_move", "manual_protect", "manual_delete"))
    client.force_login(user)

    body = _get(client, "manuals:manual_detail", pk)[1]
    actions, menu = _actions(body), _more(body)
    outside = actions[: actions.index(MORE_MENU)]
    assert f'href="{reverse("manuals:manual_pdf", args=[pk])}"' in outside
    assert f'href="{reverse("manuals:manualversion_list", args=[pk])}"' in outside
    for url in (move, protect, delete):
        assert f'href="{url}"' in menu and body.count(f'href="{url}"') == 1, url
    last = menu.rsplit("<li>", 1)[1]
    assert f'href="{delete}"' in last and "text-error" in last and "削除</a>" in last

    manuals_services.protect_manual(manual, True, user)
    body = _get(client, "manuals:manual_detail", pk)[1]
    menu = _more(body)
    assert f'href="{protect}"' in menu and "保護解除</a>" in menu
    assert move not in body and delete not in body

    # 書く権限の無い人 (編集の判定を効かせた設定の、Access の行の無い利用者) には「⋯」が無い
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    body = _get(client, "manuals:manual_detail", pk)[1]
    assert "data-detail-more" not in body


def _chips(body):
    """頭の要点の札の (名, 値) の並び。"""
    found = re.findall(
        r'<span class="badge badge-outline gap-1 border-base-300" data-head-chip><span class="text-base-content/70">([^<]+)</span>'
        r'<span class="font-medium tabular-nums">([^<]+)</span></span>',
        body,
    )
    assert len(found) == body.count("data-head-chip")
    return found


@pytest.mark.django_db
def test_head_chips_on_order_and_purchase_order(logged_in, client, settings, item, order, purchase_order):
    body = _get(logged_in, "orders:order_detail", order.pk)[1]
    assert _chips(body) == [("得意先", "作り物の得意先"), ("一番早い納期", "2026-11-12"), ("合計", f"¥{comma(Decimal('15600'))}")]
    # 札は状態の印より後ろ (h1 の後の最初の印は状態の印)
    after_h1 = body[body.index('<h1 class="truncate') :]
    assert after_h1.index('class="badge') < after_h1.index("data-head-chip")
    assert not after_h1[after_h1.index('class="badge') :].startswith('class="badge badge-outline gap-1 border-base-300" data-head-chip')

    body = _get(logged_in, "purchasing:purchaseorder_detail", purchase_order.pk)[1]
    assert [label for label, _ in _chips(body)] == ["協力企業", "一番早い納期", "合計"]
    heading = body[body.index('<div id="po-heading"') : body.index('id="po-alert"')]
    assert heading.count("data-head-chip") == 3 and "作り物の協力企業" in heading

    body = _get(logged_in, "masters:item_detail", item.pk)[1]
    assert "data-head-chip" not in body

    # 金額の閲覧の無い人は「合計」の札が無い
    settings.SEIZOU = {"access_control": dict(test_detail_columns.MONEY_ON)}
    client.force_login(test_detail_columns._account("genba"))
    body = _get(client, "orders:order_detail", order.pk)[1]
    assert [label for label, _ in _chips(body)] == ["得意先", "一番早い納期"]


def test_manual_intro_has_detail_head_section():
    intro = (Path(seizou.core.__file__).parent / "manual_default" / "index.html").read_text(encoding="utf-8")
    assert "<h3>詳細の画面</h3>" in intro
    after = intro[intro.index("<h3>詳細の画面</h3>") :]
    paragraph = after[after.index("<p>") : after.index("</p>")]
    for word in ("パンくず", "⋯", "流れ"):
        assert word in paragraph, word
