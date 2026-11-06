"""詳細と小窓のタブの select_related (tabs の 3 つ目。N174)。

tabs の要素は (見出し, related_name) か (見出し, related_name, ("FK の名前", ...))。3 つ目があれば、土台の related_tab が
行と相手を 1 回の SELECT で引くので、行の __str__ が FK を読むタブでも、行の数だけ SELECT が出ない。
問い合わせの数は Django の test client の要求と CaptureQueriesContext で見る (最初の要求は設定の控えを読むので、数える前に 1 回開く)。
名前と品番は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services as billing_services
from seizou.core.views import _tab_specs, related_tab
from seizou.masters import services as masters_services
from seizou.masters.views import ItemModalView
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services


def selects(queries) -> int:
    return sum(1 for query in queries if query["sql"].lstrip().upper().startswith("SELECT"))


def count_selects(client, url, **headers) -> int:
    """url を開いた 1 回の要求の SELECT の数 (前に 1 回開いて、設定の控えを読み終えておく)。"""
    assert client.get(url, **headers).status_code == 200
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url, **headers)
    assert response.status_code == 200
    return selects(ctx.captured_queries)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "N174-1", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def processes(user):
    return [masters_services.create_process({"code": f"P{n}", "name": f"作り物の工程{n}"}, user) for n in range(1, 5)]


# ---------------------------------------------------------------- tabs の読み方


def test_tab_specs_reads_pairs_and_triples():
    tabs = (("取引先ごとの品番", "partner_items"), ("標準の工程", "item_processes", ("process",)), ("明細", "lines", ["shipment"]))

    assert _tab_specs(tabs) == [
        ("取引先ごとの品番", "partner_items", ()),
        ("標準の工程", "item_processes", ("process",)),
        ("明細", "lines", ("shipment",)),
    ]
    assert _tab_specs(()) == []


@pytest.mark.django_db
def test_related_tab_with_names_reads_rows_and_fk_at_once(user, item, processes):
    def tab_selects(names, chosen) -> int:
        with CaptureQueriesContext(connection) as ctx:
            tab = related_tab(item, "標準の工程", "item_processes", names)
            texts = [str(row["obj"]) for row in tab["rows"]]
        assert texts == [f"N174-1 {p.code} {p.name}" for p in chosen]
        return selects(ctx.captured_queries)

    masters_services.set_item_processes(item, processes[:1], user)
    plain_one, named_one = tab_selects((), processes[:1]), tab_selects(("process",), processes[:1])
    masters_services.set_item_processes(item, processes, user)
    plain_four, named_four = tab_selects((), processes), tab_selects(("process",), processes)

    assert plain_four > plain_one  # 名前が無ければ、行の字が工程を読む SELECT が行の数だけ出る (今までどおり)
    assert named_four == named_one  # 名前があれば、行の数で増えない


@pytest.mark.django_db
def test_related_tab_without_names_keeps_rows_and_count(user, item, processes):
    masters_services.set_item_processes(item, processes, user)

    plain = related_tab(item, "標準の工程", "item_processes")
    named = related_tab(item, "標準の工程", "item_processes", ("process",))

    assert plain["label"] == named["label"] == "標準の工程"
    assert plain["related"] == named["related"] == "item_processes"
    assert plain["count"] == named["count"] == 4
    assert [row["obj"].pk for row in plain["rows"]] == [row["obj"].pk for row in named["rows"]]
    assert [row["url"] for row in plain["rows"]] == [row["url"] for row in named["rows"]]


# ---------------------------------------------------------------- 詳細 (3 つ目を書いたタブ)


@pytest.mark.django_db
def test_item_detail_process_tab_selects_do_not_grow_with_rows(logged_in, user, item, processes):
    url = reverse("masters:item_detail", args=[item.pk])

    masters_services.set_item_processes(item, processes[:1], user)
    one = count_selects(logged_in, url)
    masters_services.set_item_processes(item, processes, user)
    four = count_selects(logged_in, url)

    assert four == one
    html = logged_in.get(url).content.decode()
    for process in processes:
        assert f"N174-1 {process.code} {process.name}" in html


def _invoice_with_lines(user, count: int):
    """納品書の出た出荷 count 件をまとめた請求書 (明細は count 行)。"""
    customer = masters_services.create_partner(
        {"code": f"C17{count}", "name": f"作り物の得意先{count}", "role": "customer", "closing_day": 20}, user
    )
    item = masters_services.create_item({"part_no": f"N174-B{count}", "name": "作り物の板", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=date(2026, 11, 2)
    )
    line = order.lines.get()
    shipments = []
    for _ in range(count):
        shipment = shipping_services.ship(line, Decimal("1"), date(2026, 11, 5), user)
        shipping_services.create_delivery_note(customer, [shipment], user)
        shipments.append(shipment)
    return billing_services.create_invoice(customer, shipments, user, issued_on=date(2026, 11, 30))


@pytest.mark.django_db
def test_invoice_detail_lines_tab_selects_do_not_grow_with_rows(logged_in, user):
    one = _invoice_with_lines(user, 1)
    four = _invoice_with_lines(user, 4)
    assert one.lines.count() == 1 and four.lines.count() == 4

    one_selects = count_selects(logged_in, reverse("billing:invoice_detail", args=[one.pk]))
    four_selects = count_selects(logged_in, reverse("billing:invoice_detail", args=[four.pk]))

    assert four_selects == one_selects
    html = logged_in.get(reverse("billing:invoice_detail", args=[four.pk])).content.decode()
    for invoice_line in four.lines.all():
        assert str(invoice_line) in html


# ---------------------------------------------------------------- 2 つ組の tabs は今までどおり


@pytest.mark.django_db
def test_two_item_tabs_render_as_before(settings, client):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    manager = _account("n174soumu", manage_accounts=True)
    dept = accounts_services.create_department("作り物の製造", ["masters"], [], None)
    _account("n174genba", departments=[dept])
    client.force_login(manager)

    response = client.get(reverse("accounts:department_detail", args=[dept.pk]))

    assert response.status_code == 200
    (tab,) = response.context["tabs"]
    assert (tab["label"], tab["related"], tab["count"]) == ("所属する人", "members", 1)
    assert [row["obj"].user.get_username() for row in tab["rows"]] == ["n174genba"]
    html = response.content.decode()
    for word in ("所属する人", "n174genba"):
        assert word in html, word


def _account(login_id, *, departments=(), **flags):
    accounts_services.issue_account(login_id, "kumo-tests-2611", list(departments), [], flags, None)
    return get_user_model().objects.get(username=login_id)


# ---------------------------------------------------------------- 小窓 (BaseModalView)


@pytest.mark.django_db
def test_modal_triple_tab_selects_do_not_grow_with_rows(monkeypatch, logged_in, user, item, processes):
    monkeypatch.setattr(ItemModalView, "tabs", (("標準の工程", "item_processes", ("process",)),))
    url = reverse("masters:item_modal", args=[item.pk])

    masters_services.set_item_processes(item, processes[:1], user)
    one = count_selects(logged_in, url, HTTP_HX_REQUEST="true")
    masters_services.set_item_processes(item, processes, user)
    four = count_selects(logged_in, url, HTTP_HX_REQUEST="true")

    assert four == one
    html = logged_in.get(url, HTTP_HX_REQUEST="true").content.decode()
    for process in processes:
        assert f"N174-1 {process.code} {process.name}" in html


@pytest.mark.django_db
def test_modal_pair_tab_renders_as_before(logged_in, user, item):
    partner = masters_services.create_partner({"code": "S174", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    masters_services.map_partner_item(partner, item, "XP-174", user)

    response = logged_in.get(reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    (tab,) = response.context["tabs"]
    assert (tab["label"], tab["related"], tab["count"]) == ("取引先ごとの品番", "partner_items", 1)
    html = response.content.decode()
    for word in ("取引先ごとの品番", "作り物の協力企業", "XP-174"):
        assert word in html, word
