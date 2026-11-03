"""精査 A6 の後始末 (N88): 届いた注文の明細の数の末尾の 0・休日の form の必須の文・/favicon.ico の口・ダッシュボードの流れの案内の行き先。

Django の test client と関数で見る (本物のブラウザは使わない)。HTTP を送らない。取引先と品目は作り物。
"""

import re
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse

from seizou.connect.models import Inbox
from seizou.connect.templatetags.connect_tags import payload_number
from seizou.core import dashboard
from seizou.core.screens import all_screens
from seizou.core.templatetags.seizou_tags import comma
from seizou.masters import services as masters_services
from seizou.masters.forms import HolidayForm
from seizou.masters.models import Holiday


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


def order_payload(lines):
    """届いた注文の本文 (tests/test_edi_wording.py の inbox の形。明細だけを差し替える)。"""
    return {
        "id": str(uuid.uuid4()),
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-0001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "S001", "name": "作り物の受ける会社"},
        "note": "",
        "lines": lines,
    }


def payload_line(line_no, part_no, qty, unit_price):
    return {
        "line_no": line_no,
        "part_no": part_no,
        "part_name": "作り物の軸",
        "qty": qty,
        "unit": "個",
        "unit_price": unit_price,
        "due_date": "2026-11-20",
        "note": "",
    }


# ---------------------------------------------------------------- 3. 届いた注文の明細の数の末尾の 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("50.00", Decimal("50.00")),
        (" 7 ", Decimal("7")),
        (50, Decimal(50)),
        ("abc", "abc"),
        ("", ""),
        ("NaN", "NaN"),
    ],
)
def test_payload_number_reads_decimal_text(value, expected):
    result = payload_number(value)
    assert result == expected
    assert type(result) is type(expected)


def test_payload_number_passes_other_values_through():
    assert payload_number(None) is None
    assert payload_number(True) is True
    assert payload_number(1.5) == 1.5  # float は読まない
    assert isinstance(payload_number(1.5), float)
    assert comma(payload_number("50.00")) == "50"
    assert comma(payload_number("1234.50")) == "1,234.5"
    assert comma(payload_number("abc")) == "abc"
    assert comma(payload_number(None)) == ""


def test_inbox_detail_and_accept_drop_trailing_zeros(logged_in, customer):
    inbox = Inbox.objects.create(
        message_id=uuid.uuid4(),
        partner=customer,
        kind="order",
        payload=order_payload([payload_line(1, "T-100", "50.00", "45.00"), payload_line(2, "T-200", "1234.50", "abc")]),
    )
    for name in ("connect:inbox_detail", "connect:inbox_accept"):
        response = logged_in.get(reverse(name, args=[inbox.pk]))
        assert response.status_code == 200, name
        html = response.content.decode()
        # 明細の表 (_payload_lines.html) の見出しの「行番号」から表の終わりまで
        table = html.split('<th class="whitespace-nowrap text-right font-medium">行番号</th>', 1)[1].split("</table>", 1)[0]
        for shown in (">50<", ">45<", ">1,234.5<", ">abc<"):
            assert shown in table, (name, shown)
        for raw in ("50.00", "45.00", "1234.50", "None"):
            assert raw not in table, (name, raw)


# ---------------------------------------------------------------- 4. 休日の form の必須の文

HOLIDAY_REQUIRED = {
    "date": ["休日の日付を入れてください。"],
    "name": ["休日の名前を入れてください。"],
    "kind": ["種類 (祝日か会社の休み) を選んでください。"],
}
DJANGO_REQUIRED = "このフィールドは必須です"  # tests/test_edi_wording.py と同じ字


@pytest.mark.django_db
def test_holiday_form_required_messages_are_japanese():
    assert HolidayForm(data={}).errors == HOLIDAY_REQUIRED


def test_holiday_create_shows_japanese_required_messages(logged_in):
    response = logged_in.post(reverse("masters:holiday_create"), {})
    assert response.status_code == 200
    html = response.content.decode()
    for messages in HOLIDAY_REQUIRED.values():
        assert messages[0] in html
    assert DJANGO_REQUIRED not in html
    assert Holiday.objects.count() == 0


# ---------------------------------------------------------------- 6. ダッシュボードの流れの案内の行き先

SALES_LABELS = ["受注", "指示書", "出荷", "納品書", "請求書", "入金"]
PURCHASE_LABELS = ["発注", "注文回答", "出荷の知らせ", "受入", "検収"]


def sales_step(key):
    flow = next(flow for flow in dashboard.FLOWS if flow["key"] == "sales")
    return next(step for step in flow["steps"] if step["key"] == key)


@pytest.mark.django_db
def test_flow_steps_go_to_counted_screens():
    assert sales_step("order")["url_name"] == "connect:inbox_list"
    assert sales_step("workorder")["url_name"] == "orders:orderline_list"
    guide = dashboard.flow_guide()
    flows = {flow["key"]: flow for flow in guide["flows"]}
    steps = {step["label"]: step for step in flows["sales"]["steps"]}
    assert steps["受注"]["url"] == reverse("connect:inbox_list")
    assert steps["指示書"]["url"] == reverse("orders:orderline_list")
    assert [step["label"] for step in flows["sales"]["steps"]] == SALES_LABELS
    assert [step["label"] for step in flows["purchase"]["steps"]] == PURCHASE_LABELS


def test_flow_neighbors_follow_new_targets():
    inbox = dashboard.flow_neighbors("connect:inbox_list")
    assert inbox["label"] == "受注"
    assert inbox["prev"] is None
    assert inbox["next"] == {"label": "指示書", "url": reverse("orders:orderline_list")}
    lines = dashboard.flow_neighbors("orders:orderline_list")
    assert lines["label"] == "指示書"
    assert lines["prev"] == {"label": "受注", "url": reverse("connect:inbox_list")}
    assert lines["next"]["label"] == "出荷"
    assert dashboard.flow_neighbors("orders:order_list") is None
    assert dashboard.flow_neighbors("workorders:workorder_list") is None


def test_home_flow_links_go_to_inbox_and_orderlines(logged_in, customer):
    Inbox.objects.create(message_id=uuid.uuid4(), partner=customer, kind="order", payload={})
    body = logged_in.get(reverse("core:home")).content.decode()
    section = re.search(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', body, re.S).group(0)
    inbox_url = reverse("connect:inbox_list")
    lines_url = reverse("orders:orderline_list")
    assert f'href="{inbox_url}"' in section
    assert f'href="{lines_url}"' in section
    assert f'次にすること: <a class="link link-hover text-primary" href="{inbox_url}">受注</a>' in section


# ---------------------------------------------------------------- 5. /favicon.ico の口


def test_favicon_ico_redirects_without_login(client):
    assert reverse("core:favicon") == "/favicon.ico"
    response = client.get("/favicon.ico")
    assert response.status_code == 302
    assert response["Location"] == "/static/favicon.svg"
    assert finders.find("favicon.svg") is not None


def test_favicon_is_not_a_screen_and_link_is_single(logged_in):
    assert "core:favicon" not in {screen.url_name for screen in all_screens()}
    login_html = Client().get(reverse("core:login")).content.decode()
    assert login_html.count('rel="icon"') == 1
    home_html = logged_in.get(reverse("core:home")).content.decode()
    assert home_html.count('rel="icon"') == 1
