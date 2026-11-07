"""詳細の基本情報の値を、関わる伝票の詳細へ移る link にする (初見テスト R5 の V69。候補 C80)。

出荷の「受注の明細」「納品書」、受入の「発注の明細」、指示書の「受注の明細」、マニュアルの「フォルダ」が、
同じタブで移る link (class="link link-primary") になる。明細は親の伝票 (受注・発注) へ移る (model の detail_link_target)。
マスター (部品と取引先) は今の小窓の link、人は字のまま。部署の判定が on のとき、部署でその部品を使わない人には字のまま。
部署の判定は access.can_open の record=False (log でも記録の行を書かない)。会社と人と品番は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from seizou.core import access
from seizou.core.models import Company
from seizou.core.views import BaseDetailView
from seizou.manuals import services as manuals_services
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from tests import test_orders_board
from tests.test_core_access import account
from tests.test_core_department_buttons import _departments
from tests.test_orders_board import _issue, _line

# 注残の盤の試験の fixture を借りる (pytest は module の名前で fixture を見つける。部署のボタンの試験と同じ形)。
user = test_orders_board.user
customer = test_orders_board.customer
item = test_orders_board.item
cut = test_orders_board.cut

#: 部署の判定だけを変え、編集の権限と金額の閲覧は on のまま (部署のボタンの試験と同じ)
WRITE_AND_MONEY_ON = {"write": "on", "money": "on"}

User = get_user_model()


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "7001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


def _open(client, person, url):
    """person でログインし直して url を開く (access_for の控えは利用者の物ごとなので、毎回表から取り直す)。"""
    client.force_login(User.objects.get(pk=person.pk))
    response = client.get(url)
    assert response.status_code == 200, url
    return response


def _field(response, label):
    """context の fields から、見出し label の項目を 1 つ引く (並びはほかの依頼で動くので、見出しで引く)。"""
    found = [field for field in response.context["fields"] if field["label"] == label]
    assert len(found) == 1, label
    return found[0]


def _dd(response, label):
    """基本の表の、見出し label の行の <dd> の中身 (閉じの </dd> まで)。"""
    body = response.content.decode()
    start = body.index(f">{label}</dt>")
    dd = body.index("<dd", start)
    return body[dd : body.index("</dd>", dd)]


def _link(url, text):
    return f'<a class="link link-primary" href="{url}">{escape(str(text))}</a>'


def _shipment(user, customer, item, qty="4"):
    return shipping_services.ship(_line(user, customer, item), Decimal(qty), timezone.localdate(), user)


def _receipt(user, subcontractor, item):
    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("250"), "due_date": date(2026, 11, 9)}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    return purchasing_services.receive(po.lines.get(), Decimal("4"), date(2026, 11, 3), user)


def _manual(user):
    folder = manuals_services.create_folder(None, "作り物の置き場", "gray", user)
    return manuals_services.create_manual(folder, "作り物の手順", "steps", user)


# ---------------------------------------------------------------- (a) (f) 出荷の詳細


@pytest.mark.django_db
def test_shipment_order_line_links_to_order_detail(client, user, customer, item):
    """(a) 出荷の「受注の明細」は受注の詳細への link。(f) 「部品」は今の小窓の link のまま。"""
    shipment = _shipment(user, customer, item)
    line = shipment.order_line
    response = _open(client, user, reverse("shipping:shipment_detail", args=[shipment.pk]))

    order_url = reverse("orders:order_detail", args=[line.order_id])
    assert _field(response, "受注の明細")["url"] == order_url
    assert _link(order_url, line) in _dd(response, "受注の明細")

    part = _field(response, "部品")
    assert part["url"] is None and part["master"] == item
    assert 'class="link master-link"' in _dd(response, "部品")
    assert "link-primary" not in _dd(response, "部品")


# ---------------------------------------------------------------- (b) 納品書


@pytest.mark.django_db
def test_shipment_delivery_note_links_only_when_noted(client, user, customer, item):
    """(b) 納品書を作った出荷の「納品書」は納品書の詳細への link。納品書の無い出荷は「—」のままで url が None。"""
    noted = _shipment(user, customer, item)
    plain = _shipment(user, customer, item, qty="3")
    note = shipping_services.create_delivery_note(customer, [noted], user)

    response = _open(client, user, reverse("shipping:shipment_detail", args=[noted.pk]))
    note_url = reverse("shipping:deliverynote_detail", args=[note.pk])
    assert _field(response, "納品書")["url"] == note_url
    assert _link(note_url, note) in _dd(response, "納品書")

    response = _open(client, user, reverse("shipping:shipment_detail", args=[plain.pk]))
    assert _field(response, "納品書")["url"] is None
    dd = _dd(response, "納品書")
    assert "—" in dd and "<a" not in dd


# ---------------------------------------------------------------- (c) 受入の詳細


@pytest.mark.django_db
def test_receipt_purchase_order_line_links_to_purchase_order(client, user, subcontractor, item):
    """(c) 受入の「発注の明細」は発注の詳細への link。「入れた人」は人の詳細が無いので字のまま。"""
    receipt = _receipt(user, subcontractor, item)
    response = _open(client, user, reverse("purchasing:receipt_detail", args=[receipt.pk]))

    po_url = reverse("purchasing:purchaseorder_detail", args=[receipt.line.purchase_order_id])
    assert _field(response, "発注の明細")["url"] == po_url
    assert _link(po_url, receipt.line) in _dd(response, "発注の明細")

    assert _field(response, "入れた人")["url"] is None
    assert "<a" not in _dd(response, "入れた人")


# ---------------------------------------------------------------- (d) 指示書の詳細


@pytest.mark.django_db
def test_workorder_order_line_links_to_order_detail(client, user, customer, item, cut):
    """(d) 指示書の「受注の明細」は受注の詳細への link (指示書のテンプレートが block.super で基本の表を出す)。"""
    line = _line(user, customer, item)
    workorder = _issue(user, line, cut, qty="4")
    response = _open(client, user, reverse("workorders:workorder_detail", args=[workorder.pk]))

    order_url = reverse("orders:order_detail", args=[line.order_id])
    assert _field(response, "受注の明細")["url"] == order_url
    assert _link(order_url, line) in _dd(response, "受注の明細")


# ---------------------------------------------------------------- (e) マニュアルの詳細


@pytest.mark.django_db
def test_manual_folder_links_to_folder(client, user):
    """(e) フォルダに入れたマニュアルの「フォルダ」は、フォルダの道 (manuals:manualfolder_detail) への link。"""
    manual = _manual(user)
    response = _open(client, user, reverse("manuals:manual_detail", args=[manual.pk]))

    folder_url = reverse("manuals:manualfolder_detail", args=[manual.folder_id])
    assert _field(response, "フォルダ")["url"] == folder_url
    assert _link(folder_url, manual.folder) in _dd(response, "フォルダ")


# ---------------------------------------------------------------- (g) (j) 全部の項目が鍵 url を持つ


@pytest.mark.django_db
def test_every_detail_field_has_url_key(client, user, customer, item, cut, subcontractor):
    """(g) 詳細の fields の全部の項目が鍵 url を持つ (部品の行と会社ごとの項目も。無ければ None)。"""
    shipment = _shipment(user, customer, item)
    shipping_services.create_delivery_note(customer, [shipment], user)
    urls = [
        reverse("shipping:shipment_detail", args=[shipment.pk]),
        reverse("purchasing:receipt_detail", args=[_receipt(user, subcontractor, item).pk]),
        reverse("workorders:workorder_detail", args=[_issue(user, _line(user, customer, item), cut, qty="4").pk]),
        reverse("manuals:manual_detail", args=[_manual(user).pk]),
        reverse("masters:item_detail", args=[item.pk]),
    ]
    for url in urls:
        fields = _open(client, user, url).context["fields"]
        assert fields, url
        assert [field["label"] for field in fields if "url" not in field] == [], url


@pytest.mark.django_db
def test_field_items_without_request_keeps_working():
    """(j) request の無い BaseDetailView() の field_items は今のとおり通る (URL の無い値では request を読まない)。"""
    company = Company.objects.create(name="作り物の会社", theme="light")
    items = BaseDetailView().field_items(company)
    assert items
    assert [field["url"] for field in items] == [None] * len(items)


# ---------------------------------------------------------------- (h) (i) 部署の判定


def _workorder_page(user, customer, item, cut):
    line = _line(user, customer, item)
    workorder = _issue(user, line, cut, qty="4")
    return reverse("workorders:workorder_detail", args=[workorder.pk]), reverse("orders:order_detail", args=[line.order_id])


@pytest.mark.django_db
def test_department_on_outsider_sees_order_line_as_text(settings, client, user, customer, item, cut):
    """(h) 部署の判定 on: 受注を使わない製造の部署の人の指示書の詳細では、「受注の明細」は字のまま (url が None・href が無い)。

    受注と出荷の部署の人では link (詳細の画面は部署を見ないので、どちらも指示書の詳細は開く)。
    """
    settings.SEIZOU = {"access_control": {"department": "on", **WRITE_AND_MONEY_ON}}
    url, order_url = _workorder_page(user, customer, item, cut)
    depts = _departments()

    seizo = _open(client, account("seizo", departments=[depts["seizo"]]), url)
    assert _field(seizo, "受注の明細")["url"] is None
    assert f'href="{order_url}"' not in seizo.content.decode()
    assert "<a" not in _dd(seizo, "受注の明細")

    sales = _open(client, account("jimu", departments=[depts["sales"]], permissions=["slip_edit"]), url)
    assert _field(sales, "受注の明細")["url"] == order_url
    assert f'<a class="link link-primary" href="{order_url}">' in _dd(sales, "受注の明細")


@pytest.mark.django_db
def test_department_log_links_without_access_record(settings, client, caplog, user, customer, item, cut):
    """(i) 部署の判定 log: 製造の部署の人の指示書の詳細で「受注の明細」は link になり、部署の記録の行は増えない (record=False)。

    受注を使う部署の人は、can_open が記録の枝の前に真を返すので、ここでは見ない。
    同じ人が記録ありの判定 (record の既定) を通ると行が 1 つ増えることも見て、記録の見方が空振りしていないことを確かめる。
    """
    settings.SEIZOU = {"access_control": {"department": "log", **WRITE_AND_MONEY_ON}}
    url, order_url = _workorder_page(user, customer, item, cut)
    seizo = account("seizo", departments=[_departments()["seizo"]])

    def department_records():
        return [r for r in caplog.records if r.name == "seizou.access" and "(部署)" in r.getMessage()]

    response = _open(client, seizo, url)
    assert _field(response, "受注の明細")["url"] == order_url
    assert f'<a class="link link-primary" href="{order_url}">' in _dd(response, "受注の明細")
    assert department_records() == []

    assert access.can_open(User.objects.get(pk=seizo.pk), "orders") is True
    assert len(department_records()) == 1
