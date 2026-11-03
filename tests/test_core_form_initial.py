"""入力の土台 (BaseFormView.get_initial) が、新規登録の画面の GET の値を form の初期値にする約束。

写すのは GET の鍵の全部 (会社ごとの項目 ext__ を除く)。form に無い鍵も写し、form の __init__ が読める。POST と編集の画面では写さない。
受注の明細から出荷の新規登録・指示書の発行へ、明細を選んだ形で移る入口 (?order_line=<pk>) に使う。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from seizou.core.views import BaseFormView
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

ORDER_DATE = date(2026, 11, 2)


def _service(data, user, *, ext=None):
    return data


class NameForm(forms.Form):
    name = forms.CharField(label="名前")


class NameView(BaseFormView):
    screen_name = "試験の入力"
    form_class = NameForm
    service = staticmethod(_service)
    success_url = "/done/"
    template_name = "core/form.html"


def _view(request, obj=None):
    view = NameView()
    view.setup(request)
    if obj is not None:
        view.object = obj
    return view


def test_get_value_becomes_initial():
    request = RequestFactory().get("/x/", {"name": "X"})
    assert _view(request).get_initial() == {"name": "X"}


def test_unknown_key_is_copied_but_ext_is_not():
    # form に無い鍵も写す (form の __init__ が self.initial.get("from_line") で読む)。会社ごとの項目 (ext__) は写さない。
    request = RequestFactory().get("/x/", {"other": "1", "ext__color": "赤"})
    assert _view(request).get_initial() == {"other": "1"}


def test_post_does_not_copy_get_values():
    request = RequestFactory().post("/x/?name=X", {})
    assert request.GET.get("name") == "X"
    assert _view(request).get_initial() == {}


def test_update_screen_keeps_object_values():
    request = RequestFactory().get("/x/", {"name": "X"})
    # 対象がある (編集の画面) ときは、GET の値を写さない。
    assert _view(request, obj=object()).get_initial() == {}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def line(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _order_line_select(html: str) -> str:
    """HTML から受注の明細の選ぶ欄 (<select name="order_line" ...>...</select>) を取り出す。"""
    match = re.search(r'<select name="order_line".*?</select>', html, re.S)
    assert match, "受注の明細の選ぶ欄が無い"
    return match.group(0)


@pytest.mark.django_db
def test_shipment_create_opens_with_line_selected(logged_in, line):
    response = logged_in.get(reverse("shipping:shipment_create") + f"?order_line={line.pk}")
    assert response.status_code == 200
    select = _order_line_select(response.content.decode())
    assert f'<option value="{line.pk}" selected>{line}</option>' in select


@pytest.mark.django_db
def test_workorder_issue_opens_with_line_selected(logged_in, line):
    response = logged_in.get(reverse("workorders:workorder_create") + f"?order_line={line.pk}")
    assert response.status_code == 200
    select = _order_line_select(response.content.decode())
    assert f'<option value="{line.pk}" selected>' in select
    assert select.count(" selected>") == 1


@pytest.mark.django_db
def test_bad_value_does_not_break_screen(logged_in, line):
    for name in ("shipping:shipment_create", "workorders:workorder_create"):
        for value in ("abc", str(line.pk + 1000)):
            response = logged_in.get(reverse(name), {"order_line": value})
            assert response.status_code == 200, (name, value)
            assert " selected>" not in _order_line_select(response.content.decode()), (name, value)
