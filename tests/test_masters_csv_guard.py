"""部品と取引先の CSV の取り込みの守り (N94): 金額の閲覧の無い人の標準単価・取引先の CSV の読むだけの列・取り込みの後の知らせ・文字コードの欄の文。名前は作り物だけ。"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.masters import services
from seizou.masters.forms import ItemCsvImportForm, PartnerCsvImportForm
from seizou.masters.models import Item
from tests.test_masters_csv_import import csv_bytes, make_form, post_csv

NO_MONEY = {"department": "off", "write": "off", "money": "on"}
WITH_MONEY = {"department": "off", "write": "off", "money": "off"}
SKIPPED = "標準単価の列は金額の閲覧の権限が無いので読みませんでした。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def saved_messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------- 2. 金額の閲覧の無い人の標準単価 (精査 A2 の B3)


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["標準単価 (円)", "標準単価"])
def test_item_import_without_money_keeps_standard_price(settings, client_logged_in, user, no_registration, header):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    services.create_item({"part_no": "P-1001", "name": "作り物の軸", "standard_price": Decimal("480.00")}, user)
    content = csv_bytes(f"自社品番,品名,{header}", "P-1001,作り物の軸,9999", "P-1002,作り物の板,abc")

    response = post_csv(client_logged_in, "masters:item_import", content)

    assert response.status_code == 302
    assert Item.objects.get(part_no="P-1001").standard_price == Decimal("480.00")
    assert Item.objects.get(part_no="P-1002").standard_price is None  # 読まない列の字 abc は誤りにならない
    notes = saved_messages(response)
    assert len(notes) == 1 and SKIPPED in notes[0], notes


@pytest.mark.django_db
def test_item_import_with_money_changes_standard_price(settings, client_logged_in, user, no_registration):
    settings.SEIZOU = {"access_control": dict(WITH_MONEY)}
    services.create_item({"part_no": "P-1001", "name": "作り物の軸", "standard_price": Decimal("480.00")}, user)
    content = csv_bytes("自社品番,品名,標準単価 (円)", "P-1001,作り物の軸,9999")

    response = post_csv(client_logged_in, "masters:item_import", content)

    assert response.status_code == 302
    assert Item.objects.get(part_no="P-1001").standard_price == Decimal("9999.00")
    notes = saved_messages(response)
    assert notes and not any(SKIPPED in note for note in notes), notes


# ---------------------------------------------------------------- 1a. 取引先の CSV が読むだけの列「注文の入口」を読まない


@pytest.mark.django_db
def test_partner_import_skips_order_inlet_column(user, no_registration):
    services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    form = make_form(
        PartnerCsvImportForm,
        csv_bytes(
            "取引先コード,名前,取引先の種類,注文の入口",
            "C001,作り物の得意先,得意先,seizou で直接",
            "C002,作り物の商会,得意先,どれでもない字",
        ),
    )

    assert form.is_valid(), form.import_errors or form.errors
    assert services.import_partners(**form.service_kwargs(), user=user) == {"入れた": 1, "更新した": 1}
    assert "注文の入口" not in form.header_hint

    item_form = make_form(ItemCsvImportForm, csv_bytes("自社品番,品名,注文の入口", "P-1001,作り物の軸,手入力"))

    assert not item_form.is_valid()
    assert any("注文の入口" in reason for _line, _header, reason in item_form.import_errors), item_form.import_errors


# ---------------------------------------------------------------- 3. 取り込みの後の知らせ (入れた数と直した数)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "content"),
    [
        ("masters:item_import", csv_bytes("自社品番,品名", "P-1001,作り物の軸", "P-1002,作り物の板")),
        ("masters:partner_import", csv_bytes("取引先コード,名前,取引先の種類", "C001,作り物の得意先,得意先", "C002,作り物の商会,得意先")),
    ],
)
def test_import_message_counts_new_and_updated(client_logged_in, user, no_registration, url_name, content):
    services.create_item({"part_no": "P-1001", "name": "作り物の軸"}, user)
    services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)

    response = post_csv(client_logged_in, url_name, content)

    assert response.status_code == 302
    assert saved_messages(response) == ["保存しました。1 件入れ、1 件直しました。"]


# ---------------------------------------------------------------- 4. 文字コードの欄を付けずに送ったときの文


@pytest.mark.django_db
def test_import_without_encoding_asks_to_choose(client_logged_in, no_registration):
    upload = SimpleUploadedFile("import.csv", csv_bytes("自社品番,品名", "P-1001,作り物の軸"), content_type="text/csv")

    response = client_logged_in.post(reverse("masters:item_import"), {"file": upload})

    body = response.content.decode()
    assert response.status_code == 200
    assert "文字コードを選んでください。" in body
    assert "このフィールドは必須です。" not in body
    assert Item.objects.count() == 0
