"""流れの枠の点の絵記号と、いつも下線の link (決め 11-05 16:2x。N163)。

受注と発注の流れの module の KIND_ICONS (出来事の種類 → core の seizou_icons の絵の名前) と、詳細の画面の流れの節の字を見る。
作り物のデータは、受注と発注の流れの試験の作り方 (services で作る) を借りる。
"""

import re

import pytest
from django.urls import reverse

from seizou.core.templatetags.seizou_icons import PATHS
from seizou.orders import timeline as orders_timeline
from seizou.purchasing import timeline as purchasing_timeline
from tests import test_orders_timeline, test_purchasing_timeline

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
