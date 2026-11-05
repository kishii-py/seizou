"""状態の色の表 (案 4-C・8-A。N152)。core.status_colors (状態の値 → 意味の色)・印の tag status_badge・
土台の詳細の見出しの印・一覧の状態の列の印・最初の画面の最近の受注の印。

表が全部の部品の model の状態 (choices を持つ status) の値を覆うことを、木 (Django の apps) から集めて見る
(部品に状態の値が足されて表に無ければ落ちる)。作り物のデータは全部 services で作る。相手の会社に HTTP を送らない
(send=False・つながりなし。requests.post は呼んだら落とす物に差し替える)。
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.template import Context, Template
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import status_colors
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

BADGE_TEMPLATE = Path(status_colors.__file__).parent / "templates" / "core" / "_status_badge.html"

#: 状態 (choices を持つ status) がある model (N152 を起こした時の木。増えたら足す)
STATUS_MODELS = {
    "orders.Order",
    "connect.Inbox",
    "purchasing.PurchaseOrder",
    "shipping.DeliveryNote",
    "workorders.WorkOrder",
    "billing.Invoice",
    "billing.Payment",
    "intake.Extraction",
    "feedback.Feedback",
}

#: 詳細の見出しの印 (点つき)
DOT_INFO = '<span class="badge badge-soft badge-info gap-2"><span class="status status-info" aria-hidden="true"></span>{}</span>'
DOT_GHOST = '<span class="badge badge-ghost gap-2"><span class="status" aria-hidden="true"></span>{}</span>'
DOT_OUTLINE = '<span class="badge badge-outline text-base-content/50 gap-2"><span class="status" aria-hidden="true"></span>{}</span>'


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def supplier(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


def _status_values() -> dict[str, set[str]]:
    """木の model のうち、choices を持つ status の項目がある物 → 値の集まり (土台の status_for と同じ読み方)。"""
    found = {}
    for model in apps.get_models():
        if not model.__module__.startswith("seizou."):
            continue
        field = next((f for f in model._meta.concrete_fields if f.name == "status" and f.choices), None)
        if field is not None:
            found[model._meta.label] = {str(value) for value, _ in field.flatchoices}
    return found


def _fake(value, label, app=None):
    """status と get_status_display と _meta.app_label だけを持つ作り物の 1 件 (tag は model を問わない)。"""
    return SimpleNamespace(status=value, get_status_display=lambda: label, _meta=SimpleNamespace(app_label=app))


def _render(obj, **options) -> str:
    args = "".join(f" {key}={value}" for key, value in options.items())
    return Template("{% load seizou_tags %}{% status_badge obj" + args + " %}").render(Context({"obj": obj})).strip()


def _order(user, customer, item):
    return orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )


def _purchase_order(user, supplier, item):
    lines = [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)}]
    return purchasing_services.create_purchase_order(supplier, lines, user, order_date=date(2026, 11, 2), send=False)


def _workorder(user, order, process):
    return workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": process, "planned_qty": Decimal("10")}], user
    )


def _invoice(user, customer, item):
    """出荷 1 つ (数量 10) の請求書 (納品書を先に作る = 発行の納品書に入った出荷だけが請求書に入る)。"""
    order = _order(user, customer, item)
    shipment = shipping_services.ship(order.lines.get(), Decimal("10"), date(2026, 11, 3), user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 4))


def _heading(client, url):
    """詳細の見出しの帯の題と状態の印 (<h1 から、題と印を包む div の閉じまで)。"""
    response = client.get(url)
    assert response.status_code == 200, url
    body = response.content.decode()
    start = body.index("<h1")
    return body[start : body.index("</div>", start)]


def _tr(body, pk):
    """一覧の行 (<tr id="row-<pk>" ...>...</tr>) の HTML。"""
    start = body.index(f'<tr id="row-{pk}"')
    return body[start : body.index("</tr>", start)]


def test_table_covers_every_status_choice():
    found = _status_values()

    assert STATUS_MODELS <= set(found), sorted(STATUS_MODELS - set(found))
    values = set().union(*found.values())
    missing = sorted(values - set(status_colors.STATUS_TONES))
    assert missing == [], f"状態の色の表に無い値: {missing} (core/status_colors.py の STATUS_TONES に足す)"
    stale = sorted(set(status_colors.STATUS_TONES) - values)
    assert stale == [], f"どの model にも無い値: {stale}"
    assert len(values) == 18
    by_app = {}
    for label, app_values in found.items():
        by_app.setdefault(label.split(".")[0], set()).update(app_values)
    for key in status_colors.APP_TONES:
        app, value = key.split(".")
        assert value in by_app.get(app, set()), f"APP_TONES の {key} を持つ model が無い"


def test_tones_are_five_meanings_and_the_template_has_a_branch_for_each():
    tones = set(status_colors.STATUS_TONES.values()) | set(status_colors.APP_TONES.values())
    assert tones == set(status_colors.TONES)
    assert status_colors.FALLBACK not in status_colors.TONES
    text = BADGE_TEMPLATE.read_text(encoding="utf-8")
    for tone in status_colors.TONES:
        assert text.count(f'tone == "{tone}"') == 1, tone
    assert "{{" not in "".join(part.split('">', 1)[0] for part in text.split('class="')[1:])  # class の値に {{ }} が無い (R19)
    assert status_colors.tone_for("作り物の値") == status_colors.tone_for(None) == status_colors.FALLBACK
    assert status_colors.tone_for("作り物の値", "connect") == status_colors.FALLBACK


@pytest.mark.parametrize(
    ("value", "label", "html"),
    [
        ("draft", "下書き", '<span class="badge badge-ghost">下書き</span>'),
        ("open", "未出荷", '<span class="badge badge-soft badge-info">未出荷</span>'),
        ("partial", "一部出荷", '<span class="badge badge-soft badge-warning">一部出荷</span>'),
        ("shipped", "出荷済", '<span class="badge badge-soft badge-success">出荷済</span>'),
        ("cancelled", "取消", '<span class="badge badge-outline text-base-content/50">取消</span>'),
        ("作り物の値", "作り物の状態", '<span class="badge badge-soft badge-primary">作り物の状態</span>'),
    ],
)
def test_tag_renders_whole_classes_by_meaning(value, label, html):
    assert _render(_fake(value, label)) == html


def test_tag_dot_and_small():
    issued = _fake("issued", "発行")

    assert _render(issued, dot=True) == DOT_INFO.format("発行")
    assert _render(issued, small=True) == '<span class="badge badge-soft badge-info badge-sm">発行</span>'
    assert _render(_fake("cancelled", "取消"), dot=True) == DOT_OUTLINE.format("取消")
    assert _render(_fake("作り物の値", "作り物の状態"), dot=True) == (
        '<span class="badge badge-soft badge-primary gap-2"><span class="status status-primary" aria-hidden="true"></span>作り物の状態</span>'
    )


def test_app_key_comes_first_for_accepted():
    """同じ字で部品により意味が違う値 (accepted): 受信した注文は終わり = 緑、発注は途中 = 黄 (本体の選び 11-05 09:0x)。"""
    assert status_colors.tone_for("accepted", "connect") == "success"
    assert status_colors.tone_for("accepted", "purchasing") == status_colors.tone_for("accepted") == "warning"
    assert _render(_fake("accepted", "受け入れた", "connect")) == '<span class="badge badge-soft badge-success">受け入れた</span>'
    assert _render(_fake("accepted", "相手が受け入れた", "purchasing")) == (
        '<span class="badge badge-soft badge-warning">相手が受け入れた</span>'
    )


@pytest.mark.django_db
def test_detail_heading_badge_uses_the_meaning_color(user, logged_in, customer, supplier, item, cut):
    order = _order(user, customer, item)
    workorder = _workorder(user, order, cut)
    purchase = _purchase_order(user, supplier, item)
    invoice = _invoice(user, customer, item)
    cases = [
        (reverse("orders:order_detail", args=[order.pk]), DOT_INFO.format("未出荷")),
        (reverse("workorders:workorder_detail", args=[workorder.pk]), DOT_INFO.format("発行")),
        (reverse("purchasing:purchaseorder_detail", args=[purchase.pk]), DOT_GHOST.format("下書き")),
        (reverse("billing:invoice_detail", args=[invoice.pk]), DOT_INFO.format("発行")),
    ]

    for url, badge in cases:
        heading = _heading(logged_in, url)
        assert badge in heading, url
        assert "badge-primary" not in heading and "status-primary" not in heading, url

    billing_services.cancel_invoice(invoice, user)
    assert DOT_OUTLINE.format("取消") in _heading(logged_in, reverse("billing:invoice_detail", args=[invoice.pk]))


@pytest.mark.django_db
def test_list_status_column_uses_the_meaning_color(user, logged_in, customer, supplier, item, cut):
    order = _order(user, customer, item)
    cancelled = orders_services.cancel_order(_order(user, customer, item), user, reason="作り物の取消")
    workorder = _workorder(user, order, cut)
    purchase = _purchase_order(user, supplier, item)
    invoice = _invoice(user, customer, item)
    cases = [
        ("orders:order_list", order.pk, '<span class="badge badge-soft badge-info">未出荷</span>'),
        ("orders:order_list", cancelled.pk, '<span class="badge badge-outline text-base-content/50">取消</span>'),
        ("workorders:workorder_list", workorder.pk, '<span class="badge badge-soft badge-info">発行</span>'),
        ("purchasing:purchaseorder_list", purchase.pk, '<span class="badge badge-ghost">下書き</span>'),
        ("billing:invoice_list", invoice.pk, '<span class="badge badge-soft badge-info">発行</span>'),
    ]

    for name, pk, badge in cases:
        row = _tr(logged_in.get(reverse(name), {"f_open": "0"}).content.decode(), pk)
        assert badge in row, name
        assert "badge-primary" not in row, name


@pytest.mark.django_db
def test_only_the_status_column_cell_is_marked(user, logged_in, customer, item):
    _order(user, customer, item)

    response = logged_in.get(reverse("orders:order_list"))
    names = [col.name for col in response.context["columns"]]
    flags = [cell["status"] for cell in response.context["rows"][0]["cells"]]
    assert [name for name, flag in zip(names, flags, strict=True) if flag] == ["status"]

    items = logged_in.get(reverse("masters:item_list"))
    assert items.context["rows"]
    assert not any(cell["status"] for row in items.context["rows"] for cell in row["cells"])


@pytest.mark.django_db
def test_home_recent_orders_use_the_meaning_color(user, logged_in, customer, item):
    _order(user, customer, item)
    orders_services.cancel_order(_order(user, customer, item), user, reason="作り物の取消")

    body = logged_in.get(reverse("core:home")).content.decode()
    # 見るのは <main> の中 (ダッシュボードの中身) だけ。左のメニュー (core/_sidebar.html) は </main> の後に描かれ、
    # 入口の横の待っている数の印 (N146) が主色の badge なので、</main> で切って除く。
    main = body.split("<main", 1)[1].split("</main>", 1)[0]

    assert '<span class="badge badge-soft badge-info badge-sm">未出荷</span>' in main
    assert '<span class="badge badge-outline text-base-content/50 badge-sm">取消</span>' in main
    assert "badge-soft badge-primary" not in main
