"""画面の土台 (seizou.core.views) の約束: ログイン、入力の土台と検査の層、小窓の link。"""

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse

from seizou.core import checks
from seizou.core.exceptions import RuleViolation
from seizou.core.views import OVERRIDABLE, BaseFormView

CALLS: list[dict] = []


def body(response) -> str:
    # RequestFactory で直接呼ぶと TemplateResponse は未描画のまま返るので、ここで描く。
    if hasattr(response, "render"):
        response.render()
    return response.content.decode()


def fake_service(data, user, *, ext=None):
    CALLS.append({"data": data, "user": user, "ext": ext})
    return data


def failing_service(data, user, *, ext=None):
    raise RuleViolation("R02", "数量と単価は 0 より大きい。数を確かめてください。")


class QtyForm(forms.Form):
    qty = forms.IntegerField(label="数量")


class QtyView(BaseFormView):
    screen_name = "試験の入力"
    form_class = QtyForm
    service = staticmethod(fake_service)
    success_url = "/done/"
    template_name = "core/form.html"


class FailingView(QtyView):
    service = staticmethod(failing_service)


class _Over(checks.Check):
    name = "T10"
    action = "tests.fake_service"

    def __init__(self, mode):
        self.mode = mode

    def run(self, data, user):
        return "テストの知らせ" if data.get("qty", 0) > 10 else None


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def rf_post(user):
    factory = RequestFactory()

    def make(data, headers=None):
        request = factory.post("/x/", data, **(headers or {}))
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        request.user = user
        return request

    return make


@pytest.fixture(autouse=True)
def _clean():
    CALLS.clear()
    checks.clear("tests.fake_service")
    yield
    checks.clear("tests.fake_service")


def test_overridable_is_the_eleven_names():
    assert set(OVERRIDABLE) == {
        "model", "ordering", "columns", "search_fields", "filters", "presets", "tabs", "form_class", "service",
        "template_name", "get_queryset", "screen_name", "screen_help",
    }
    assert len(OVERRIDABLE) == 13


@pytest.mark.django_db
def test_login_page_renders(client):
    response = client.get(reverse("core:login"))
    assert response.status_code == 200
    assert "ログイン" in body(response)


@pytest.mark.django_db
def test_action_is_derived_from_service_module_and_name():
    assert QtyView().get_action() == "tests.fake_service"


@pytest.mark.django_db
def test_form_view_calls_service_and_redirects(rf_post):
    response = QtyView.as_view()(rf_post({"qty": "3"}))
    assert response.status_code == 302
    assert response["Location"] == "/done/"
    assert CALLS == [{"data": {"qty": 3}, "user": CALLS[0]["user"], "ext": None}]


@pytest.mark.django_db
def test_form_view_htmx_redirects_with_header(rf_post):
    response = QtyView.as_view()(rf_post({"qty": "3"}, {"HTTP_HX_REQUEST": "true"}))
    assert response.status_code == 200
    assert response["HX-Redirect"] == "/done/"


@pytest.mark.django_db
def test_warn_shows_notice_and_waits_for_confirmation(rf_post):
    checks.register(_Over("warn"))
    response = QtyView.as_view()(rf_post({"qty": "11"}))
    assert response.status_code == 200
    body_text = body(response)
    assert "テストの知らせ" in body_text
    assert "このまま保存" in body_text
    assert CALLS == []
    # もう一度押す (confirmed=1) と通る
    response = QtyView.as_view()(rf_post({"qty": "11", "confirmed": "1"}))
    assert response.status_code == 302
    assert len(CALLS) == 1


@pytest.mark.django_db
def test_stop_blocks_and_keeps_values(rf_post):
    checks.register(_Over("stop"))
    response = QtyView.as_view()(rf_post({"qty": "11"}))
    assert response.status_code == 200
    body_text = body(response)
    assert '<span class="font-semibold">保存できません。</span> テストの知らせ' in body_text  # 会社の検査の名前は出さない (N156)
    assert 'value="11"' in body_text
    assert CALLS == []


@pytest.mark.django_db
def test_rule_violation_from_service_is_red_notice(rf_post):
    response = FailingView.as_view()(rf_post({"qty": "3"}))
    assert response.status_code == 200
    body_text = body(response)
    assert "R02" in body_text and 'data-notice="stop"' in body_text
