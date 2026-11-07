"""部品の単位を数量単位の一覧から選ぶ (決め UNIT-1 = a。R5 の候補 C108 の その 1)。

一覧の表 (masters.QuantityUnit) の 40 行は migration だけが入れるので、試験は transaction=True にしない (migration の入れた行を読むため)。
部品は services.create_item で作る (単位の名前から数量単位コードを引く道を通す)。作り物のデータだけを使う。
"""

import importlib
import re
from pathlib import Path

import django.apps
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import seizou.masters.migrations
from seizou.accounts import services as accounts_services
from seizou.core import fields as company_fields
from seizou.masters import services
from seizou.masters.forms import ItemCsvImportForm
from seizou.masters.models import Item, QuantityUnit

#: 中小企業共通 EDI の付表 4 の数量単位コードの表 (国際サブセット 15 行と＜参考＞の表 25 行) の字のまま
EDI_UNITS = [
    ("H87", "個"), ("SET", "セット"), ("TNE", "t"), ("KGM", "kg"), ("GRM", "g"), ("MGM", "mg"),
    ("LTR", "l"), ("MLT", "ml"), ("KTM", "km"), ("MTR", "m"), ("CMT", "cm"), ("MMT", "mm"),
    ("MTK", "m²"), ("CMK", "cm²"), ("MTQ", "m³"),
    ("T3", "千個"), ("PG", "枚（物品）"), ("XST", "枚（紙）"), ("QB", "ページ（書物）"),
    ("XBO", "本（瓶）"), ("XTU", "本（チューブ）"), ("PI", "本（パイプ）"), ("XRD", "本（棒）"),
    ("XRO", "巻（ロール）"), ("XCL", "巻（コイル）"), ("RL", "リール"), ("XCN", "缶"),
    ("HEA", "人・頭（動物）"), ("Q3", "食（食事）"), ("XCS", "ケース"), ("XCT", "カートン"),
    ("KT", "キット"), ("XBX", "箱"), ("PX", "台（パレット）"), ("XPK", "包"), ("XBG", "袋（俵）"),
    ("XBK", "籠"), ("PR", "組"), ("DZN", "ダース"), ("GRO", "グロス"),
]
UNLISTED_NOTICE = "単位「本」は単位の一覧にありません。"
PASSWORD = "kumo-tests-2611"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def _item(user, part_no, unit, name="作り物の軸"):
    return services.create_item({"part_no": part_no, "name": name, "unit": unit}, user)


def _unit_pair(item):
    item.refresh_from_db()
    return item.unit, item.unit_code


def _migration(suffix):
    """masters の migration を名前の後ろで探して読む (番号は他の依頼で変わるため)。"""
    [path] = Path(seizou.masters.migrations.__file__).parent.glob(f"*_{suffix}.py")
    return importlib.import_module(f"seizou.masters.migrations.{path.stem}")


def _select_tag(body, name):
    match = re.search(rf'<select name="{name}"[^>]*>', body)
    assert match, name
    return match.group(0)


# ---------------------------------------------------------------- (a) 一覧の値


@pytest.mark.django_db
def test_units_are_the_edi_table_in_order():
    rows = list(QuantityUnit.objects.values_list("code", "name"))
    assert rows == EDI_UNITS
    assert len(rows) == 40
    assert rows[0] == ("H87", "個")
    assert all(len(code) <= 10 and len(name) <= 10 for code, name in rows)
    folded = [services._fold(name) for _code, name in rows]
    assert len(set(folded)) == len(folded)


# ---------------------------------------------------------------- (b) 名前とコードを引く


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("個", "H87"),
        ("kg", "KGM"),
        ("KG", "KGM"),
        ("ｋｇ", "KGM"),
        ("㎏", "KGM"),
        ("m2", "MTK"),
        ("㎡", "MTK"),
        ("本(棒)", "XRD"),
        (" 箱 ", "XBX"),
        ("本", ""),
        ("枚", ""),
        ("巻", ""),
        ("pcs", ""),
        ("KGM", ""),
        ("", ""),
    ],
)
def test_unit_code_for(name, code):
    assert services.unit_code_for(name) == code


@pytest.mark.django_db
@pytest.mark.parametrize(("code", "name"), [("KGM", "kg"), ("ZZZ", ""), ("", "")])
def test_unit_name_for(code, name):
    assert services.unit_name_for(code) == name


# ---------------------------------------------------------------- (c) 部品を書く所


@pytest.mark.django_db
def test_create_and_update_item_write_unit_code(user):
    assert _unit_pair(_item(user, "P-1", "個")) == ("個", "H87")
    assert _unit_pair(_item(user, "P-2", "KG")) == ("kg", "KGM")
    unlisted = _item(user, "P-3", "本")
    assert _unit_pair(unlisted) == ("本", "")
    assert unlisted.unit_unlisted is True

    kept = _item(user, "P-4", "kg")
    services.update_item(kept, {"name": "別の名前"}, user)
    assert _unit_pair(kept) == ("kg", "KGM")
    services.update_item(kept, {"unit": ""}, user)
    assert _unit_pair(kept) == ("", "")
    assert kept.unit_unlisted is False


@pytest.mark.django_db
def test_create_item_refuses_unit_code_key(user):
    with pytest.raises(ValueError, match="知らない項目"):
        services.create_item({"part_no": "P-1", "name": "作り物の軸", "unit": "個", "unit_code": "H87"}, user)


# ---------------------------------------------------------------- (d) 入力の画面


@pytest.mark.django_db
def test_create_screen_has_unit_select(logged_in):
    body = logged_in.get(reverse("masters:item_create")).content.decode()
    tag = _select_tag(body, "unit")
    assert 'id="id_unit"' in tag
    assert f'data-select-url="{reverse("masters:quantityunit_select")}"' in tag
    assert "list=" not in tag


@pytest.mark.django_db
def test_create_screen_saves_chosen_unit(logged_in):
    response = logged_in.post(reverse("masters:item_create"), {"part_no": "P-1", "name": "作り物の軸", "unit": "KGM"})
    assert response.status_code == 302
    assert _unit_pair(Item.objects.get(part_no="P-1")) == ("kg", "KGM")

    response = logged_in.post(reverse("masters:item_create"), {"part_no": "P-2", "name": "作り物の板", "unit": "ZZZ"})
    assert response.status_code == 200
    assert "unit" in response.context["form"].errors
    assert not Item.objects.filter(part_no="P-2").exists()

    response = logged_in.post(reverse("masters:item_create"), {"part_no": "P-3", "name": "作り物の座金"})
    assert response.status_code == 302
    assert _unit_pair(Item.objects.get(part_no="P-3")) == ("", "")


# ---------------------------------------------------------------- (e) 編集


def _edit_post(item, **values):
    return {"part_no": item.part_no, "name": item.name, **values}


@pytest.mark.django_db
def test_edit_screen_selects_current_unit(logged_in, user):
    item = _item(user, "P-1", "kg")
    body = logged_in.get(reverse("masters:item_update", args=[item.pk])).content.decode()
    assert '<option value="KGM" selected>kg</option>' in body

    # 一覧の単位があった部品で欄を空にして保存したら、単位を空にする
    response = logged_in.post(reverse("masters:item_update", args=[item.pk]), _edit_post(item))
    assert response.status_code == 302
    assert _unit_pair(item) == ("", "")


@pytest.mark.django_db
def test_edit_screen_keeps_unlisted_unit_unless_chosen(logged_in, user):
    item = _item(user, "P-1", "本")
    url = reverse("masters:item_update", args=[item.pk])
    body = logged_in.get(url).content.decode()
    assert "今の単位「本」は一覧にありません。" in body
    assert not re.findall(r'<option value="[^"]+" selected>', body)

    response = logged_in.post(url, _edit_post(item))
    assert response.status_code == 302
    assert _unit_pair(item) == ("本", "")

    response = logged_in.post(url, _edit_post(item, unit="XRD"))
    assert response.status_code == 302
    assert _unit_pair(item) == ("本（棒）", "XRD")


# ---------------------------------------------------------------- (f) 候補の口


@pytest.mark.django_db
def test_unit_candidates(logged_in):
    url = reverse("masters:quantityunit_select")
    data = logged_in.get(url, {"q": "kg"}).json()
    assert data["items"][0] == {"id": "KGM", "text": "kg", "title": "kg", "sub": "KGM"}

    data = logged_in.get(url, {"q": "本"}).json()
    assert [row["id"] for row in data["items"]] == ["XBO", "XTU", "PI", "XRD"]

    first = logged_in.get(url).json()
    assert len(first["items"]) == 30
    assert first["more"] is True
    assert first["recent"] == []
    assert first["create"] is None
    second = logged_in.get(url, {"page": "2"}).json()
    assert len(second["items"]) == 10
    assert second["more"] is False


# ---------------------------------------------------------------- (f2) 部品の候補の口と一覧の母集団


@pytest.mark.django_db
def test_item_candidates_carry_unit_code(logged_in, user):
    listed = _item(user, "P-1", "個")
    unlisted = _item(user, "P-2", "袋")
    rows = {row["id"]: row for row in logged_in.get(reverse("masters:item_select"), {"q": "P-"}).json()["items"]}
    assert rows[listed.pk]["unit_code"] == "H87"
    assert rows[unlisted.pk]["unit_code"] == ""


@pytest.mark.django_db
def test_quantity_units_and_unlisted_message():
    assert list(services.quantity_units().values_list("code", "name")) == EDI_UNITS
    assert services.UNLISTED_LINE_UNIT.format(unit="本").startswith("部品の単位「本」は単位の一覧にありません。")


# ---------------------------------------------------------------- (g) 一覧の絞りと黄色の 1 行


@pytest.mark.django_db
def test_list_filter_and_yellow_line(logged_in, user):
    unlisted = _item(user, "U-HON", "本")
    listed = _item(user, "U-KO", "個")

    body = logged_in.get(reverse("masters:item_list"), {"f_unit_unlisted": "1"}).content.decode()
    assert "U-HON" in body and "U-KO" not in body
    body = logged_in.get(reverse("masters:item_list")).content.decode()
    assert "U-HON" in body and "U-KO" in body

    detail = logged_in.get(reverse("masters:item_detail", args=[unlisted.pk])).content.decode()
    modal = logged_in.get(reverse("masters:item_modal", args=[unlisted.pk]), HTTP_HX_REQUEST="true").content.decode()
    for body in (detail, modal):
        assert UNLISTED_NOTICE in body
        assert "alert-warning" in body

    detail = logged_in.get(reverse("masters:item_detail", args=[listed.pk])).content.decode()
    modal = logged_in.get(reverse("masters:item_modal", args=[listed.pk]), HTTP_HX_REQUEST="true").content.decode()
    for body in (detail, modal):
        assert "alert-warning" not in body
    assert "数量単位コード" in detail
    assert "H87" in detail


# ---------------------------------------------------------------- (g2) 小窓の項目


MONEY_FIELDS = ["自社品番", "品名", "図面番号", "図面ファイル", "単位", "標準単価 (円)", "備考", "使用中"]
NO_MONEY_FIELDS = ["自社品番", "品名", "図面番号", "図面ファイル", "単位", "備考", "使用中", "数量単位コード"]


def _modal_labels(client, item):
    response = client.get(reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    return [str(field["label"]) for field in response.context["fields"]]


@pytest.mark.django_db
def test_modal_fields_with_and_without_money(client, settings, user, no_registration):
    item = _item(user, "P-1", "個")
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    accounts_services.issue_account("keiri", PASSWORD, [], [], {"see_money": True}, None)
    accounts_services.issue_account("jimu", PASSWORD, [], [], {}, None)

    client.force_login(get_user_model().objects.get(username="keiri"))
    assert _modal_labels(client, item) == MONEY_FIELDS
    client.force_login(get_user_model().objects.get(username="jimu"))
    assert _modal_labels(client, item) == NO_MONEY_FIELDS


# ---------------------------------------------------------------- (h) CSV


def _import(content, user):
    upload = SimpleUploadedFile("import.csv", ("\r\n".join(content) + "\r\n").encode("utf-8"), content_type="text/csv")
    form = ItemCsvImportForm(data={"encoding": "utf-8-sig"}, files={"file": upload})
    assert form.is_valid(), form.import_errors or form.errors
    return services.import_items(**form.service_kwargs(), user=user)


@pytest.mark.django_db
def test_csv_import_keeps_unlisted_unit_and_warns(user, no_registration):
    result = _import(["自社品番,品名,単位", "P-1,作り物の軸,KG", "P-2,作り物の板,本"], user)
    assert result == {"入れた": 2, "更新した": 0}
    assert _unit_pair(Item.objects.get(part_no="P-1")) == ("kg", "KGM")
    assert _unit_pair(Item.objects.get(part_no="P-2")) == ("本", "")
    assert result.saved_level == "warning"
    assert "単位が一覧に無い部品が 1 件あります。" in result.saved_note


@pytest.mark.django_db
def test_csv_import_of_listed_units_stays_green(user, no_registration):
    result = _import(["自社品番,品名,単位", "P-1,作り物の軸,個"], user)
    assert getattr(result, "saved_level", "") == ""
    assert "単位が一覧に無い" not in result.saved_note


# ---------------------------------------------------------------- (i) 移す関数


@pytest.mark.django_db
def test_migration_moves_item_units_to_the_list():
    migration = _migration("quantityunit")
    kg = Item.objects.create(part_no="P-1", name="作り物の軸", unit="KG")
    hon = Item.objects.create(part_no="P-2", name="作り物の棒", unit="本")
    blank = Item.objects.create(part_no="P-3", name="作り物の板", unit="")

    migration.fill_item_codes(django.apps.apps, None)

    assert _unit_pair(kg) == ("kg", "KGM")
    assert _unit_pair(hon) == ("本", "")
    assert _unit_pair(blank) == ("", "")

    migration.seed_units(django.apps.apps, None)
    migration.seed_units(django.apps.apps, None)
    assert QuantityUnit.objects.count() == 40
