"""流れの枠の点の絵記号と、いつも下線の link (決め 11-05 16:2x。N163)。

受注と発注の流れの module の KIND_ICONS (出来事の種類 → core の seizou_icons の絵の名前) と、詳細の画面の流れの節の字を見る。
作り物のデータは、受注と発注の流れの試験の作り方 (services で作る) を借りる。
"""

import re
import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.core.dashboard import INLETS
from seizou.core.templatetags.seizou_icons import PATHS
from seizou.orders import timeline as orders_timeline
from seizou.purchasing import timeline as purchasing_timeline
from tests import test_orders_timeline, test_purchasing_timeline
from tests.test_r10_inbox_accept import _line, _payload

# 流れの試験の作り物を借りる (pytest は module の名前で fixture を見つける)。
customer = test_orders_timeline.customer
item = test_orders_timeline.item
cut = test_orders_timeline.cut
subcontractor = test_purchasing_timeline.subcontractor
send_connection = test_purchasing_timeline.send_connection


def _section(body, section_id):
    start = body.index(f'<section id="{section_id}"')
    return body[start : body.index("</section>", start)]


def _detail(client, name, pk):
    response = client.get(reverse(name, args=[pk]))
    assert response.status_code == 200
    return response.content.decode()


def _icons(section):
    return re.findall(r'data-flow-icon="([^"]+)"', section)


def test_every_kind_has_an_existing_icon():
    for module in (orders_timeline, purchasing_timeline):
        assert set(module.KIND_ICONS) == set(module.KINDS), module.__name__
        assert set(module.KIND_ICONS.values()) <= set(PATHS), module.__name__


@pytest.mark.django_db
def test_flow_rows_show_icons_and_underlined_links(monkeypatch, user, logged_in, customer, item, cut, subcontractor, send_connection):
    order = test_orders_timeline._flowed(user, customer, item, cut)[0]
    events = orders_timeline.events_for(order)

    section = _section(_detail(logged_in, "orders:order_detail", order.pk), "order-flow")

    assert _icons(section) == [e.icon for e in events]
    assert section.count("<svg") >= len(events)
    assert "link-hover" not in section and 'class="link link-primary"' in section

    po = test_purchasing_timeline._flowed(monkeypatch, user, subcontractor, item)[0]
    events = purchasing_timeline.events_for(po)
    section = _section(_detail(logged_in, "purchasing:purchaseorder_detail", po.pk), "po-flow")
    assert _icons(section) == [e.icon for e in events]
    assert "receipt" in [e.kind for e in events] and "box" in _icons(section)
    assert "link-hover" not in section and 'class="link link-primary"' in section


@pytest.mark.django_db
def test_flow_rows_show_who_and_inlet(monkeypatch, user, logged_in, customer, item, cut, subcontractor, send_connection):
    order = test_orders_timeline._flowed(user, customer, item, cut)[0]
    name = str(user)

    events = orders_timeline.events_for(order)

    assert [(e.kind, e.who, e.via) for e in events] == [
        ("order", "", "手入力"),
        ("workorder", "", ""),
        ("accepted", name, ""),
        ("progress", name, ""),
        ("shipment", "", ""),
        ("delivery_note", "", ""),
        ("invoice", "", ""),
        ("payment", name, ""),
    ]
    section = _section(_detail(logged_in, "orders:order_detail", order.pk), "order-flow")
    rows = re.findall(r"<p [^>]*data-flow-who>([^<]*)</p>", section)
    assert rows == ["手入力", name, name, name]

    po = test_purchasing_timeline._flowed(monkeypatch, user, subcontractor, item)[0]
    receipts = [e for e in purchasing_timeline.events_for(po) if e.kind == "receipt"]
    assert [e.who for e in receipts] == [name]
    assert all(e.who == "" for e in purchasing_timeline.events_for(po) if e.kind != "receipt")


@pytest.mark.django_db
def test_inbox_order_flow_names_sender_and_inlet(user, customer, item):
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload["order_date"] = timezone.localdate().isoformat()
    inbox = connect_services.record_inbox(customer, payload, user, received_at=timezone.now() - timedelta(minutes=1))
    order = connect_services.accept(inbox, {"A-100": item}, user, send=False)

    events = orders_timeline.events_for(order)

    direct = dict(INLETS)["direct"]
    assert (events[0].kind, events[0].who, events[0].via) == ("inbox", customer.name, direct)
    registered = [e for e in events if e.kind == "order"]
    assert [(e.title, e.via) for e in registered] == [("受注日 (相手の発注日)", direct)]
