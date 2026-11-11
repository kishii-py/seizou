"""部品の CSV の取り込みで、標準単価が 0 以下の行と、単位の一覧に無い単位の行を黄の知らせで並べる (N411。本体の決め 11-11 14:2x)。

断らない (入れられる)。入れる前の表 (form.import_notices) は誤りで止まった時に画面に出し、入った時は結果の知らせ (saved_note) に足す。
単位の一覧 (masters.QuantityUnit) の行は migration が入れるので、試験は transaction=True にしない。名前と品番は作り物だけ。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages

from seizou.masters.forms import ItemCsvImportForm, PartnerCsvImportForm
from seizou.masters.models import Item, Partner
from tests.test_masters_csv_guard import NO_MONEY
from tests.test_masters_csv_import import csv_bytes, import_valid, make_form, no_registration, post_csv  # noqa: F401 - fixture を借りる

PRICE_REASON = (
    "標準単価が 0 以下です。入れられますが、この部品を受注に写すと「数量と単価は 0 より大きい」(R02) で保存できません。"
)
UNIT_REASON = "単位「ダース箱」は単位の一覧にありません。入れられますが、入れた後に部品の編集で単位を選び直してください。"
FRAME_TITLE = "入れる前に確かめる行"
FRAME_LEAD = "次の行は入れられますが、確かめてください。"
UNLISTED_NOTE = "単位が一覧に無い部品が 1 件あります。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def saved_messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------- (a) 誤りで止まった時の入れる前の表


@pytest.mark.django_db
@pytest.mark.parametrize("price_header", ["標準単価", "標準単価 (円)"])
def test_stopped_import_lists_low_price_and_unlisted_unit_rows(client_logged_in, no_registration, price_header):  # noqa: F811
    content = csv_bytes(
        f"自社品番,品名,{price_header},単位",
        "P-1001,,100,個",
        "P-1002,作り物の板,-5,個",
        "P-1003,作り物の軸,480,ダース箱",
    )

    response = post_csv(client_logged_in, "masters:item_import", content)

    assert response.status_code == 200
    assert Item.objects.count() == 0
    form = response.context["form"]
    assert [line for line, _field, _reason in form.import_errors] == [2]
    assert form.import_notices == [(3, price_header, PRICE_REASON), (4, "単位", UNIT_REASON)]
    assert all("-5" not in reason for _line, _field, reason in form.import_notices)
    body = response.content.decode()
    assert FRAME_TITLE in body
    assert "alert-warning" in body
    assert FRAME_LEAD in body
    assert PRICE_REASON in body
    assert UNIT_REASON in body
    # 枠は誤りの表の後。頭に黄色の 1 行、その下に 行・欄・理由 の表。理由の字は赤にしない
    assert body.index("CSV の誤り") < body.index(FRAME_TITLE)
    frame = body[body.index(FRAME_TITLE):]
    frame = frame[: frame.index("</table>")]
    assert frame.index("alert-warning") < frame.index(FRAME_LEAD) < frame.index("<th>行</th><th>欄</th><th>理由</th>")
    assert frame.index("3 行目") < frame.index(PRICE_REASON) < frame.index("4 行目") < frame.index(UNIT_REASON)
    assert "text-error" not in frame


@pytest.mark.django_db
def test_notices_are_filled_even_without_errors(no_registration):  # noqa: F811
    content = csv_bytes("自社品番,品名,標準単価 (円),単位", "P-1001,作り物の板,0,ダース箱", "P-1002,作り物の軸,480,個")

    form = make_form(ItemCsvImportForm, content)

    assert form.is_valid(), form.import_errors or form.errors
    assert form.import_notices == [(2, "標準単価 (円)", PRICE_REASON), (2, "単位", UNIT_REASON)]


@pytest.mark.django_db
def test_listed_unit_and_blank_cells_are_not_listed(no_registration):  # noqa: F811
    content = csv_bytes(
        "自社品番,品名,標準単価 (円),単位",
        "P-1001,作り物の板,,",
        "P-1002,作り物の軸,480, kg ",
        "P-1003,作り物の輪,0.01,個",
    )

    form = make_form(ItemCsvImportForm, content)

    assert form.is_valid(), form.import_errors or form.errors
    assert form.import_notices == []


# ---------------------------------------------------------------- (b) 入った時の結果の知らせ


@pytest.mark.django_db
def test_import_result_warns_low_price_items(user, no_registration):  # noqa: F811
    content = csv_bytes(
        "自社品番,品名,標準単価 (円),単位",
        "P-1001,作り物の板,0,個",
        "P-1002,作り物の軸,-5,",
        "P-1003,作り物の輪,480,ダース箱",
    )

    _form, result = import_valid(ItemCsvImportForm, content, user)

    assert result == {"入れた": 3, "更新した": 0}
    assert Item.objects.get(part_no="P-1002").standard_price == Decimal("-5")
    assert result.saved_level == "warning"
    note = result.saved_note
    assert "標準単価が 0 以下の部品が 2 件あります (P-1001・P-1002)。" in note
    assert "R02" in note
    assert "部品の編集で直してください。" in note
    assert UNLISTED_NOTE in note
    assert note.index(UNLISTED_NOTE) < note.index("標準単価が 0 以下の部品")
    assert "-5" not in note


@pytest.mark.django_db
def test_import_result_of_positive_prices_stays_green(user, no_registration):  # noqa: F811
    content = csv_bytes("自社品番,品名,標準単価 (円),単位", "P-1001,作り物の板,480,個", "P-1002,作り物の軸,,個")

    _form, result = import_valid(ItemCsvImportForm, content, user)

    assert getattr(result, "saved_level", "") == ""
    assert "標準単価が 0 以下" not in result.saved_note


# ---------------------------------------------------------------- (c) 金額の閲覧の無い人


@pytest.mark.django_db
def test_without_money_low_price_is_not_listed_anywhere(settings, client_logged_in, no_registration):  # noqa: F811
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    stopped = csv_bytes("自社品番,品名,標準単価 (円),単位", "P-1001,,100,個", "P-1002,作り物の板,-5,ダース箱")

    response = post_csv(client_logged_in, "masters:item_import", stopped)

    assert response.status_code == 200
    form = response.context["form"]
    assert form.import_notices == [(3, "単位", UNIT_REASON)]
    assert PRICE_REASON not in response.content.decode()

    passed = csv_bytes("自社品番,品名,標準単価 (円),単位", "P-1002,作り物の板,-5,個")
    response = post_csv(client_logged_in, "masters:item_import", passed)

    assert response.status_code == 302
    assert Item.objects.get(part_no="P-1002").standard_price is None
    notes = saved_messages(response)
    assert len(notes) == 1, notes
    assert "標準単価が 0 以下" not in notes[0]


# ---------------------------------------------------------------- (d) 自社品番は 5 件まで


@pytest.mark.django_db
def test_low_price_part_numbers_are_listed_up_to_five(user, no_registration):  # noqa: F811
    lines = [f"P-10{n:02d},作り物の部品 {n},0" for n in range(1, 7)]
    content = csv_bytes("自社品番,品名,標準単価 (円)", *lines)

    _form, result = import_valid(ItemCsvImportForm, content, user)

    assert result.saved_level == "warning"
    assert (
        "標準単価が 0 以下の部品が 6 件あります (P-1001・P-1002・P-1003・P-1004・P-1005・ほか 1 件)。" in result.saved_note
    )
    assert "P-1006" not in result.saved_note


# ---------------------------------------------------------------- (e) 取引先の取り込みは変わらない


@pytest.mark.django_db
def test_partner_import_has_no_notice_frame(client_logged_in, no_registration):  # noqa: F811
    content = csv_bytes("取引先コード,名前,取引先の種類,締日", "C001,,得意先,20", "C002,作り物の得意先,得意先,0")

    response = post_csv(client_logged_in, "masters:partner_import", content)

    assert response.status_code == 200
    assert Partner.objects.count() == 0
    form = response.context["form"]
    assert form.import_errors
    assert form.import_notices == []
    body = response.content.decode()
    assert "CSV の誤り" in body
    assert FRAME_TITLE not in body


@pytest.mark.django_db
def test_partner_form_notices_start_empty(no_registration):  # noqa: F811
    form = make_form(PartnerCsvImportForm, csv_bytes("取引先コード,名前,取引先の種類", "C001,作り物の得意先,得意先"))

    assert form.is_valid(), form.import_errors or form.errors
    assert form.import_notices == []
