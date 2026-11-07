"""取引先の登録番号 (masters.Partner.registration_no) のテスト。初見テスト R5 の候補 C111 その 1。

仕入明細 (中小企業共通 EDI の仕入明細の項目を元にした) に、相手 (協力企業) の適格請求書発行事業者の登録番号を写すための欄。
- (a) services で入り、空に戻せる
- (b) 取引先の新規登録の画面に欄があり、字数は 14
- (c) 形 (T と 13 桁の数字) の違う値は、欄の下の赤い字か services の ValueError で保存されない。空は通る (決め C95-2)
- (d) 取引先の CSV の見本の見出しに「登録番号」があり、取り込みで入る。形の違う番号の行は取り込まれない
取引先と番号は作り物。
"""

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Partner

GOOD = "T0000000000001"
FORM_ERROR = "T と 13 桁の数字で入れてください。"
SERVICE_ERROR = "保存できません。登録番号は T と 13 桁の数字で入れてください。"


def _data(**changes):
    data = {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}
    data.update(changes)
    return data


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    from seizou.core import fields as company_fields

    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.mark.django_db
def test_services_write_and_clear_registration_no(user):
    """(a) create_partner で入り、update_partner で空にできる。前後の空白は落とす。"""
    partner = masters_services.create_partner(_data(registration_no=f" {GOOD} "), user)
    partner.refresh_from_db()
    assert partner.registration_no == GOOD

    masters_services.update_partner(partner, {"registration_no": ""}, user)
    partner.refresh_from_db()
    assert partner.registration_no == ""


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["T000000000001", "t0000000000001", "T００００００００００００１", "0000000000001"])
def test_services_refuse_wrong_shape(user, value):
    """(c) 形の違う値は services が ValueError で止め、取引先を作らない。"""
    with pytest.raises(ValueError) as caught:
        masters_services.create_partner(_data(registration_no=value), user)
    assert str(caught.value) == SERVICE_ERROR
    assert not Partner.objects.filter(code="S001").exists()


@pytest.mark.django_db
def test_create_screen_has_the_field(client_logged_in):
    """(b) 新規登録の画面に欄があり、字数は 14。案内の字も出る。"""
    body = client_logged_in.get(reverse("masters:partner_create")).content.decode()
    assert 'name="registration_no"' in body
    assert 'maxlength="14"' in body
    assert "仕入明細に相手の番号として載る" in body


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["T000000000001", "t0000000000001", "T００００００００００００１"])
def test_create_screen_refuses_wrong_shape(client_logged_in, value):
    """(c) 形の違う値は欄の下の赤い字で止まり、保存されない。"""
    response = client_logged_in.post(
        reverse("masters:partner_create"),
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "billing_method": "closing", "registration_no": value},
    )
    assert response.status_code == 200
    assert FORM_ERROR in response.content.decode()
    assert not Partner.objects.filter(code="S001").exists()


@pytest.mark.django_db
def test_create_screen_refuses_too_long(client_logged_in):
    """(c) 15 字は字数の誤り (欄の下の赤い字) で保存されない。"""
    response = client_logged_in.post(
        reverse("masters:partner_create"),
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "billing_method": "closing", "registration_no": "T00000000000012"},
    )
    assert response.status_code == 200
    assert response.context["form"].errors.get("registration_no")
    assert not Partner.objects.filter(code="S001").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["", GOOD])
def test_create_screen_saves_empty_and_good(client_logged_in, value):
    """(c) 空と形の合う値は保存される。"""
    response = client_logged_in.post(
        reverse("masters:partner_create"),
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "billing_method": "closing", "registration_no": value},
    )
    assert response.status_code == 302
    assert Partner.objects.get(code="S001").registration_no == value


def _partner_form(*lines):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from seizou.masters.forms import PartnerCsvImportForm

    content = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    upload = SimpleUploadedFile("import.csv", content, content_type="text/csv")
    return PartnerCsvImportForm(data={"encoding": "utf-8-sig"}, files={"file": upload})


@pytest.mark.django_db
def test_csv_sample_header_has_registration_no(client_logged_in):
    """(d) 取引先の CSV の見本の見出しに「登録番号」がある (見出しは表の verbose_name から作る)。"""
    response = client_logged_in.get(reverse("masters:partner_import_sample"))
    text = b"".join(response.streaming_content if response.streaming else [response.content]).decode("utf-8-sig")
    assert "登録番号" in text.splitlines()[0]


@pytest.mark.django_db
def test_csv_import_writes_and_refuses(user, no_registration):
    """(d) 取り込みで入る。形の違う番号の行があれば、行の番号を付けて断り、1 件も入らない (取り込みは全部か無しか)。"""
    form = _partner_form("取引先コード,名前,取引先の種類,登録番号", f"S001,作り物の協力企業 1,協力企業,{GOOD}")
    assert form.is_valid(), form.import_errors or form.errors
    masters_services.import_partners(**form.service_kwargs(), user=user)
    assert Partner.objects.get(code="S001").registration_no == GOOD

    form = _partner_form(
        "取引先コード,名前,取引先の種類,登録番号",
        f"S002,作り物の協力企業 2,協力企業,{GOOD}",
        "S003,作り物の協力企業 3,協力企業,T123",
    )
    assert form.is_valid(), form.import_errors or form.errors
    with pytest.raises(ValueError) as caught:
        masters_services.import_partners(**form.service_kwargs(), user=user)
    assert "登録番号は T と 13 桁の数字で入れてください。" in str(caught.value)
    assert not Partner.objects.filter(code__in=["S002", "S003"]).exists()
