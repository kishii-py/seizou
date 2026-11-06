"""空のまま保存したときの字を、入力の土台でまとめて見出し入りの字にする (初見テスト R5 の V37。候補 C25 の 2。決め 11-06 18:3x C25-1 = a)。

入力の土台 (BaseFormView.get_form) は全部の欄に use_label_required を通し、required の字が Django の既定のまま
(「このフィールドは必須です。」) の欄だけを「<見出し>を入れてください。」にする。選ぶ欄とファイルの欄は「<見出し>を選んでください。」、
1 つの checkbox は「<見出し>に チェックを入れてください。」。欄ごとに書いた字と隠した欄は替えない。
関数の試験の form は試験の中だけの物。画面はアカウントの発行と編集・出荷の新規登録・部品の CSV の取り込み
(入金の記録の金額は tests/test_billing_payment_views.py)。
"""

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts.models import Access
from seizou.core.views import use_label_required
from seizou.masters.models import Item
from seizou.shipping.models import Shipment
from tests.test_accounts_views import PASSWORD, _form_post, account, manager, on  # noqa: F401 - fixture を借りる
from tests.test_masters_csv_guard import no_registration  # noqa: F401 - fixture を借りる
from tests.test_shipping_views import order  # noqa: F401 - fixture を借りる

User = get_user_model()

DJANGO_REQUIRED = str(forms.Field.default_error_messages["required"])
DJANGO_REQUIRED_WORDS = "このフィールドは必須です"  # tests/test_edi_wording.py と同じ字


class _Sample(forms.Form):
    """欄の種類を 1 つずつ並べた試験の form (字・数・日付・選ぶ欄 3 つ・1 つの checkbox・欄ごとの字・見出しの無い欄・隠した欄)。"""

    name = forms.CharField(label="品名")
    qty = forms.DecimalField(label="数量")
    due = forms.DateField(label="納期")
    kind = forms.ChoiceField(label="種類", choices=[("a", "作り物の種類")])
    tags = forms.MultipleChoiceField(label="札", choices=[("a", "作り物の札")])
    drawing = forms.FileField(label="図面")
    checked = forms.BooleanField(label="確認の印")
    note = forms.CharField(label="備考", error_messages={"required": "作り物の欄ごとの字です。"})
    memo_text = forms.CharField()
    token = forms.CharField(widget=forms.HiddenInput)


def _through(form):
    """入力の土台の get_form と同じく、全部の欄に use_label_required を通す。"""
    for name, field in form.fields.items():
        use_label_required(name, field)
    return form


def _errors(form) -> dict:
    assert not form.is_valid()
    return {name: [str(text) for text in texts] for name, texts in form.errors.items()}


def test_default_required_message_gets_the_label():
    form = _through(_Sample(data={}, files={}))

    assert DJANGO_REQUIRED.startswith(DJANGO_REQUIRED_WORDS)
    assert _errors(form) == {
        "name": ["品名を入れてください。"],
        "qty": ["数量を入れてください。"],
        "due": ["納期を入れてください。"],
        "kind": ["種類を選んでください。"],
        "tags": ["札を選んでください。"],
        "drawing": ["図面を選んでください。"],
        "checked": ["確認の印に チェックを入れてください。"],
        "note": ["作り物の欄ごとの字です。"],
        "memo_text": ["Memo textを入れてください。"],
        "token": [DJANGO_REQUIRED],
    }


def test_class_fields_keep_the_default_message():
    _through(_Sample(data={}, files={}))

    second = _Sample(data={}, files={})
    assert _errors(second)["name"] == [DJANGO_REQUIRED]
    assert str(_Sample.base_fields["name"].error_messages["required"]) == DJANGO_REQUIRED


def test_optional_field_stays_optional():
    class _Optional(forms.Form):
        note = forms.CharField(label="備考", required=False)

    form = _through(_Optional(data={}))

    assert form.is_valid()
    assert form.cleaned_data == {"note": ""}


# --- 画面を通す (入力の土台の get_form が欄に通す) ---


@pytest.mark.django_db
def test_access_create_with_empty_password_names_the_field(on, client, manager):  # noqa: F811
    client.force_login(manager)

    response = client.post(reverse("accounts:access_create"), _form_post(login_id="jimu", password="", is_active="on"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "パスワードを入れてください。" in body
    assert DJANGO_REQUIRED_WORDS not in body
    assert not User.objects.filter(username="jimu").exists()


@pytest.mark.django_db
def test_access_update_with_empty_password_still_saves(on, client, manager):  # noqa: F811
    target = account("genba")
    row = target.seizou_access
    client.force_login(manager)

    response = client.post(
        reverse("accounts:access_update", args=[row.pk]), _form_post(row, password="", permissions=["workorders"])
    )

    assert response.status_code == 302
    assert Access.objects.get(pk=row.pk).write_permissions == ["workorders"]
    assert User.objects.get(pk=target.pk).check_password(PASSWORD)


@pytest.mark.django_db
def test_shipment_create_with_nothing_names_each_field(client_logged_in, order):  # noqa: F811
    response = client_logged_in.post(reverse("shipping:shipment_create"), {"order_line": "", "qty": "", "shipped_at": ""})

    body = response.content.decode()
    assert response.status_code == 200
    assert "受注の明細を選んでください。" in body
    assert "数量を入れてください。" in body
    assert "出荷日を入れてください。" in body  # 欄ごとに書いた字 (shipping/forms.py の SHIPPED_AT_ERRORS) のまま
    assert DJANGO_REQUIRED_WORDS not in body
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_item_import_without_file_asks_to_choose_the_file(client_logged_in, no_registration):  # noqa: F811
    response = client_logged_in.post(reverse("masters:item_import"), {"encoding": "utf-8-sig"})

    body = response.content.decode()
    assert response.status_code == 200
    assert "CSV のファイルを選んでください。" in body
    assert DJANGO_REQUIRED_WORDS not in body
    assert Item.objects.count() == 0
