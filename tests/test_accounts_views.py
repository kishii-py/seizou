"""アカウント管理の画面と、金額の閲覧の無い人への断り (02 の 7 節)。Django の test client で HTML と表の状態を見る。

判定は settings の fixture で 3 つとも有効にする (tests/settings.py は SEIZOU = {} のまま)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.accounts import demo_seed, services
from seizou.accounts.models import Access, AccessLog, Department
from seizou.billing import services as billing_services
from seizou.core.models import Company
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.orders.forms import NEW_LINE_WITHOUT_MONEY
from seizou.orders.models import Order, OrderLine
from seizou.shipping import services as shipping_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


def account(login_id, *, departments=(), permissions=(), **flags):
    services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


@pytest.fixture
def manager(db):
    return account("soumu", manage_accounts=True)


@pytest.fixture
def seizou_dept(db):
    return services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)


@pytest.fixture
def customer(manager):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, manager)


@pytest.fixture
def shaft(manager):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("1234")}, manager
    )


@pytest.fixture
def order(manager, customer, shaft):
    return orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        manager,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def note(manager, customer, order):
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, manager)
    return shipping_services.create_delivery_note(customer, [shipment], manager)


@pytest.fixture
def invoice(manager, customer, note):
    Company.objects.create(name="作り物の製作所")
    return billing_services.create_invoice(customer, list(note.shipments.all()), manager, issued_on=ISSUED_ON)


def _menu_labels(client):
    return [item["label"] for item in client.get(reverse("core:home")).context["seizou"]["menu"]]


def _login_ids(response):
    """一覧の行のログイン ID (上の帯に自分の ID が出るので、HTML でなく行で見る)。"""
    return [row["obj"].user.get_username() for row in response.context["rows"]]


def _form_post(row=None, **values):
    """アカウントの入力の POST の値 (check の欄は入れた物だけを送る)。"""
    data = {"login_id": "", "password": "", "departments": [], "permissions": []}
    if row is not None:
        data.update(
            {
                "departments": [d.pk for d in row.departments.all()],
                "permissions": list(row.write_permissions),
                **{key: "on" for key in ("see_money", "full_control", "manage_accounts") if getattr(row, key)},
            }
        )
        if row.user.is_active:
            data["is_active"] = "on"
    data.update(values)
    return {key: value for key, value in data.items() if value is not None}


# ---------------------------------------------------------------- アカウント管理の画面


@pytest.mark.django_db
def test_list_filters_and_deleted_toggle(on, client, manager, seizou_dept):
    genba = account("genba", departments=[seizou_dept], permissions=["slip_edit"], see_money=True)
    gone = account("taishoku")
    services.delete_account(gone.seizou_access, manager)
    client.force_login(manager)

    response = client.get(reverse("accounts:access_list"))
    assert response.status_code == 200
    html = response.content.decode()
    for word in ("genba", "製造", "伝票編集", "金額の閲覧", "使用中", "ログイン ID", "最終ログイン", "削除済みも表示"):
        assert word in html, word
    assert "taishoku" not in html

    shown = client.get(reverse("accounts:access_list") + "?deleted=1").content.decode()
    assert f"taishoku_deleted_{gone.pk}" in shown
    assert "削除済みを隠す" in shown

    filtered = client.get(reverse("accounts:access_list") + "?q=gen")
    assert _login_ids(filtered) == ["genba"]
    assert _login_ids(response) == ["genba", "soumu"]

    detail_url = reverse("accounts:access_detail", args=[genba.seizou_access.pk])
    assert f'href="{detail_url}"' in html
    moved = client.get(detail_url)
    assert moved.status_code == 302
    assert moved["Location"] == reverse("accounts:access_update", args=[genba.seizou_access.pk])


@pytest.mark.django_db
def test_gate_and_menu(on, settings, client, manager):
    plain = User.objects.create_user("tester", password=PASSWORD)
    row = manager.seizou_access
    urls = [
        reverse("accounts:access_list"),
        reverse("accounts:access_create"),
        reverse("accounts:access_update", args=[row.pk]),
        reverse("accounts:access_delete", args=[row.pk]),
    ]

    client.force_login(plain)
    for url in urls:
        response = client.get(url)
        assert response.status_code == 403, url
        assert "アカウント管理の権限が要ります" in response.content.decode()
    assert "アカウント管理" not in _menu_labels(client)
    assert reverse("accounts:access_list") not in client.get(reverse("core:settings")).content.decode()

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "off"}}
    assert client.get(urls[0]).status_code == 403
    assert "アカウント管理" not in _menu_labels(client)

    client.force_login(manager)
    for url in urls:
        assert client.get(url).status_code == 200, url
    # 左のメニューには出さず (決め 11-03 の候補 7)、設定の画面の入口から開く
    assert "アカウント管理" not in _menu_labels(client)
    assert reverse("accounts:access_list") in client.get(reverse("core:settings")).content.decode()


@pytest.mark.django_db
def test_issue_from_screen(on, client, manager, seizou_dept):
    client.force_login(manager)

    response = client.post(
        reverse("accounts:access_create"),
        _form_post(
            login_id="jimu",
            password=PASSWORD,
            departments=[seizou_dept.pk],
            permissions=["slip_edit"],
            see_money="on",
            is_active="on",
        ),
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:access_list")
    row = Access.objects.get(user__username="jimu")
    assert list(row.departments.all()) == [seizou_dept]
    assert row.write_permissions == ["slip_edit"]
    assert row.see_money is True
    assert AccessLog.objects.filter(target=row.user, action=AccessLog.ACTION_ISSUE, actor=manager).exists()
    assert authenticate(username="jimu", password=PASSWORD) is not None

    weak = client.post(reverse("accounts:access_create"), _form_post(login_id="keiri", password="1234", is_active="on"))
    assert weak.status_code == 200
    html = weak.content.decode()
    assert 'data-notice="stop"' in html
    assert "パスワードを受け付けられません" in html
    assert not User.objects.filter(username="keiri").exists()


@pytest.mark.django_db
def test_edit_keeps_password_and_disables(on, client, manager, seizou_dept):
    target = account("genba", departments=[seizou_dept])
    row = target.seizou_access
    url = reverse("accounts:access_update", args=[row.pk])
    client.force_login(manager)

    page = client.get(url).content.decode()
    assert 'value="genba"' in page
    assert "空のままなら変わりません" in page
    assert 'id="access-data"' in page
    assert reverse("accounts:access_delete", args=[row.pk]) in page

    saved = client.post(url, _form_post(row, password="", permissions=["workorders"]))
    assert saved.status_code == 302
    assert Access.objects.get(pk=row.pk).write_permissions == ["workorders"]
    assert authenticate(username="genba", password=PASSWORD) is not None

    other = client.__class__()
    other.force_login(User.objects.get(pk=target.pk))
    assert other.get(reverse("workorders:workorder_list")).status_code == 200

    stopped = client.post(url, _form_post(Access.objects.get(pk=row.pk), is_active=None))
    assert stopped.status_code == 302
    assert User.objects.get(pk=target.pk).is_active is False
    assert AccessLog.objects.filter(target=target).first().action == AccessLog.ACTION_DISABLE
    back = other.get(reverse("workorders:workorder_list"))
    assert back.status_code == 302
    assert back["Location"].startswith(reverse("core:login"))


@pytest.mark.django_db
def test_full_control_note_on_issue_and_edit(on, client, manager, seizou_dept):
    """アカウントの発行と編集の「フルコントロール」の欄の下に、範囲の注記を出す (初見テスト R5 の V116。候補 C47 の 2)。

    注記の <p> の class は見ない (C49 が替える)。
    """
    target = account("genba", departments=[seizou_dept])
    client.force_login(manager)

    for url in (reverse("accounts:access_create"), reverse("accounts:access_update", args=[target.seizou_access.pk])):
        html = client.get(url).content.decode()
        note = re.search(r'<p id="id_full_control_helptext"[^>]*>(.*?)</p>', html, re.S)
        assert note is not None, url
        for word in ("権限の判定", "アカウント管理", "システム管理者だけ"):
            assert word in note.group(1), (url, word)


@pytest.mark.django_db
def test_self_disable_is_red(on, client, manager):
    row = manager.seizou_access
    client.force_login(manager)

    response = client.post(reverse("accounts:access_update", args=[row.pk]), _form_post(row, is_active=None))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-notice="stop"' in html
    assert "自分のアカウントを使用停止にしようとしています。" in html
    assert User.objects.get(pk=manager.pk).is_active is True


@pytest.mark.django_db
def test_delete_confirm_then_delete(on, client, manager):
    target = account("genba")
    row = target.seizou_access
    url = reverse("accounts:access_delete", args=[row.pk])
    client.force_login(manager)

    page = client.get(url)
    assert page.status_code == 200
    html = page.content.decode()
    assert "アカウント「" in html and "genba" in html
    assert "削除したアカウントは元に戻せません" in html
    assert 'class="btn btn-error"' in html

    response = client.post(url, {})
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:access_list")
    assert Access.objects.get(pk=row.pk).deleted_at is not None
    assert _login_ids(client.get(reverse("accounts:access_list"))) == ["soumu"]

    again = client.post(url, {})
    assert again.status_code == 200
    assert "削除したアカウントは変更も削除もできません。" in again.content.decode()


# ---------------------------------------------------------------- 部署の一覧


@pytest.mark.django_db
def test_department_list_queries_do_not_grow(on, client, manager, seizou_dept):
    """部署の一覧の「人数」は 1 回の SQL で数える (部署が 1 つでも 4 つでも問い合わせの数は同じ。削除した人は数えない)。"""
    account("genba", departments=[seizou_dept])
    gone = account("taishoku", departments=[seizou_dept])
    services.delete_account(gone.seizou_access, manager)
    client.force_login(manager)
    url = reverse("accounts:department_list")
    client.get(url)  # 設定とセッションの控えを温める

    with CaptureQueriesContext(connection) as one:
        response = client.get(url)
    assert response.status_code == 200
    assert [row["cells"][3]["value"] for row in response.context["rows"]] == [1]

    for number in range(2, 5):
        services.create_department(f"作り物の部署 {number}", ["orders", "shipping"], [], None)
    with CaptureQueriesContext(connection) as four:
        response = client.get(url)
    assert response.status_code == 200
    assert len(four.captured_queries) == len(one.captured_queries)
    counts = {row["obj"].name: row["cells"][3]["value"] for row in response.context["rows"]}
    assert counts == {"製造": 1, "作り物の部署 2": 0, "作り物の部署 3": 0, "作り物の部署 4": 0}
    assert "人数" in response.content.decode()

    # 「人数」の見出しで並べ替えられる (既定の並びでは先頭の製造が、少ない順では末尾になる)
    assert response.context["rows"][0]["obj"].name == "製造"
    ordered = client.get(url + "?sort=member_total")
    assert ordered.context["rows"][-1]["obj"].name == "製造"


# ---------------------------------------------------------------- 金額の閲覧の無い人への断り


@pytest.mark.django_db
def test_invoice_print_pdf_need_money(on, client, invoice):
    clerk = account("jimu", permissions=["slip_edit"])
    client.force_login(clerk)
    for name in ("billing:invoice_print", "billing:invoice_pdf"):
        response = client.get(reverse(name, args=[invoice.pk]))
        assert response.status_code == 403, name
        html = response.content.decode()
        assert "この書面は開けません" in html
        assert "請求書は金額が主な内容のため、金額の閲覧の権限が無い方は開けません。" in html

    keiri = account("keiri", permissions=["billing"], see_money=True)
    client.force_login(keiri)
    for name in ("billing:invoice_print", "billing:invoice_pdf"):
        assert client.get(reverse(name, args=[invoice.pk])).status_code == 200, name


@pytest.mark.django_db
def test_order_create_refused_and_edit_keeps_price(on, client, customer, shaft, order):
    clerk = account("jimu", permissions=["orders"])
    client.force_login(clerk)
    management = {"lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000"}

    create_page = client.get(reverse("orders:order_create")).content.decode()
    assert 'name="lines-0-unit_price"' not in create_page
    refused = client.post(
        reverse("orders:order_create"),
        {
            "customer": customer.pk,
            "order_date": ORDER_DATE.isoformat(),
            "customer_order_no": "",
            **management,
            "lines-TOTAL_FORMS": "1",
            "lines-0-item": shaft.pk,
            "lines-0-qty": "3",
            "lines-0-unit": "",
            "lines-0-due_date": "",
            "lines-0-note": "",
        },
    )
    assert refused.status_code == 200
    assert "受注は金額が主な内容のため、金額の閲覧の権限が無い方は新規登録できません。" in refused.content.decode()
    assert Order.objects.count() == 1

    line = order.lines.get()
    url = reverse("orders:order_update", args=[order.pk])
    assert 'name="lines-0-unit_price"' not in client.get(url).content.decode()
    edit = {
        "customer": customer.pk,
        "order_date": ORDER_DATE.isoformat(),
        "customer_order_no": "",
        "note": "",
        **management,
        "lines-INITIAL_FORMS": "1",
        "lines-TOTAL_FORMS": "1",
        "lines-0-order_line": line.pk,
        "lines-0-item": shaft.pk,
        "lines-0-qty": "12",
        "lines-0-unit": line.unit,
        "lines-0-due_date": "",
        "lines-0-note": "",
    }
    saved = client.post(url, edit)
    assert saved.status_code == 302
    line.refresh_from_db()
    assert line.qty == Decimal("12")
    assert line.unit_price == Decimal("1500")

    added = client.post(
        url,
        {
            **edit,
            "lines-TOTAL_FORMS": "2",
            "lines-1-order_line": "",
            "lines-1-item": shaft.pk,
            "lines-1-qty": "2",
            "lines-1-unit": "",
            "lines-1-due_date": "",
            "lines-1-note": "",
        },
    )
    assert added.status_code == 200
    assert NEW_LINE_WITHOUT_MONEY in added.content.decode()
    assert OrderLine.objects.filter(order=order).count() == 1


@pytest.mark.django_db
def test_item_edit_hides_standard_price(on, client, shaft):
    clerk = account("jimu", permissions=["items"])
    client.force_login(clerk)
    url = reverse("masters:item_update", args=[shaft.pk])

    page = client.get(url).content.decode()
    assert 'name="standard_price"' not in page
    assert 'value="T-100"' in page

    response = client.post(url, {"part_no": "T-100", "name": "作り物の軸 (改)", "drawing_no": "", "unit": "個", "note": ""})
    assert response.status_code == 302
    item = Item.objects.get(pk=shaft.pk)
    assert item.name == "作り物の軸 (改)"
    assert item.standard_price == Decimal("1234")


@pytest.mark.django_db
def test_delivery_note_blank_money(on, client, note):
    clerk = account("jimu", permissions=["shipping"])
    client.force_login(clerk)
    url = reverse("shipping:deliverynote_print", args=[note.pk])

    hidden = client.get(url)
    assert hidden.status_code == 200
    html = hidden.content.decode()
    assert "T-100" in html
    assert ">4<" in html
    assert "1,500" not in html
    assert "6,000" not in html
    assert "単価" in html and "金額" in html

    keiri = account("keiri", see_money=True)
    client.force_login(keiri)
    shown = client.get(url).content.decode()
    assert "1,500" in shown
    assert "6,000" in shown


@pytest.mark.django_db
def test_part_templates_mask_money(on, client, order):
    clerk = account("jimu", permissions=["orders"])
    client.force_login(clerk)

    html = client.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert "1,500" not in html
    assert "15,000" not in html
    # 単価と金額の列は見出しごと出ない (「—」も出さない。N155)
    assert ">単価</th>" not in html and ">金額</th>" not in html

    keiri = account("keiri", see_money=True)
    client.force_login(keiri)
    shown = client.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert "1,500" in shown


# ---------------------------------------------------------------- 2 社の種


@pytest.mark.django_db
def test_demo_seed_twice(db):
    first = demo_seed.load(None)
    second = demo_seed.load(None)

    assert first == {"部署": {"入れた": 4, "飛ばした": 0}, "アカウント": {"入れた": 3, "飛ばした": 0}}
    assert second == {"部署": {"入れた": 0, "飛ばした": 4}, "アカウント": {"入れた": 0, "飛ばした": 3}}
    assert list(Department.objects.values_list("name", flat=True)) == ["製造", "受注と出荷", "経理", "総務"]
    assert Access.objects.count() == 3
    for login_id in ("genba", "jimu", "keiri"):
        assert authenticate(username=login_id, password=demo_seed.DEMO_PASSWORD) is not None, login_id
    keiri = Access.objects.get(user__username="keiri")
    assert keiri.see_money is True
    assert keiri.write_permissions == ["billing"]
    assert Access.objects.get(user__username="genba").write_permissions == []
    assert Access.objects.get(user__username="jimu").write_permissions == ["slip_edit"]
