"""取引先の入力で、支払日の欄の下に支払の月と支払日の今の値を詳細と同じ字で読み返す 1 行を出す (初見テスト R5 の候補 C84。V42・V192)。

1. 取引先の新規登録と編集の画面の全体は masters/partner-form.js を 1 回読む。部品と工程の新規登録の画面は読まない。
2. 取引先の新規登録と編集の小窓も JS を 1 回読む。工程・休日・部品の新規登録の小窓は読まない。
3. JS は字を textContent で出し、HTML として差し込まず、通信せず、聞き手を document に付けない。当日の分かれを持つ。
4. model の Partner.get_payment_day_display の注が、同じ分かれの JS があることを書いている (分かれを変えたら両方を直す)。

ブラウザの動き (打った値で出る字・隠れる時・小窓を開き直したときの行の数) は画面の手で確かめる。作り物のデータだけを使う。
"""

from pathlib import Path

import pytest
from django.urls import reverse

import seizou.masters
from seizou.masters import services as masters_services
from seizou.masters.models import Partner

PARTNER_FORM_JS = Path(seizou.masters.__file__).resolve().parent / "static" / "masters" / "partner-form.js"
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
JS_NAME = "masters/partner-form.js"


@pytest.fixture
def partner(user):
    return masters_services.create_partner(
        {"code": "P001", "name": "作り物の取引先", "role": "customer", "payment_month": "next", "payment_day": 31},
        user,
    )


def _partner_urls(partner):
    return [reverse("masters:partner_create"), reverse("masters:partner_update", args=[partner.pk])]


# ---------------------------------------------------------------- 1. 画面の全体


@pytest.mark.django_db
def test_partner_screens_read_the_payment_js(client_logged_in, partner):
    for url in _partner_urls(partner):
        response = client_logged_in.get(url)
        body = response.content.decode()
        assert response.status_code == 200, url
        assert body.count(JS_NAME) == 1, url
        assert "masters/partner_form.html" in [template.name for template in response.templates], url

    for name in ("masters:item_create", "masters:process_create"):
        response = client_logged_in.get(reverse(name))
        assert response.status_code == 200, name
        assert JS_NAME not in response.content.decode(), name


# ---------------------------------------------------------------- 2. 小窓


@pytest.mark.django_db
def test_partner_modals_read_the_payment_js(client_logged_in, partner):
    for url in _partner_urls(partner):
        response = client_logged_in.get(url, **MODAL)
        body = response.content.decode()
        assert response.status_code == 200, url
        assert "<html" not in body, url
        assert "hx-post=" in body, url
        assert body.count(JS_NAME) == 1, url
        assert "masters/_form_modal.html" in [template.name for template in response.templates], url

    for name in ("masters:process_create", "masters:holiday_create", "masters:item_create"):
        response = client_logged_in.get(reverse(name), **MODAL)
        body = response.content.decode()
        assert response.status_code == 200, name
        assert "hx-post=" in body, name
        assert JS_NAME not in body, name


# ---------------------------------------------------------------- 3. JS の字


def test_partner_form_js_writes_text_only():
    text = PARTNER_FORM_JS.read_text(encoding="utf-8")
    for word in (
        "textContent",
        "支払日 ",
        "末",
        " 日",
        "same_day",
        "NFKC",
        "currentScript",
        "selectedOptions",
        "payment_month",
        "payment_day",
        "payment-readback",
        "hidden",
    ):
        assert word in text, word
    for word in (
        "innerHTML",
        "insertAdjacentHTML",
        'document.addEventListener("input"',
        'document.addEventListener("change"',
        "fetch(",
        "XMLHttpRequest",
    ):
        assert word not in text, word


# ---------------------------------------------------------------- 4. model の注


def test_payment_day_display_names_the_js():
    assert "partner-form.js" in Partner.get_payment_day_display.__doc__
