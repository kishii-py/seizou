"""単価の行 (単価表。C98 の その 2)。表の制約・services・部品の詳細の取引先のタブ・金額の閲覧・削除・見積書。

決め: 同じ条件の行は 1 つ (C98-2)・単価と数量の下限は 0 より大きい (C98-3)・単価の行の追加と編集と削除は金額が主な登録 (C98-5・C98-6)・
部品の取引先の行の削除は金額の閲覧を見ず、その行の単価の行も一緒に消す (C98-1・C98-6)。
"""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services
from seizou.masters.models import Item, ItemPrice, Partner, PartnerItem, Process, price_basis_text

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
MONEY_ON = {"access_control": {"department": "off", "write": "off", "money": "on"}}
SAME_BAND = "保存できません。同じ条件の単価の行がもうあります (¥1,200 / 個)。改定なら適用開始日を入れてください。今の単価を直すなら、今の行を編集してください。"
SAME_BAND_HIDDEN = "保存できません。同じ条件の単価の行がもうあります。改定なら適用開始日を入れてください。今の単価を直すなら、今の行を編集してください。"


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def item(db):
    return Item.objects.create(part_no="T-100", name="作り物の軸", unit="個")


@pytest.fixture
def customer(db):
    return Partner.objects.create(code="C001", name="作り物の得意先", short_name="作り得", role="customer")


@pytest.fixture
def supplier(db):
    return Partner.objects.create(code="S001", name="作り物の協力企業", role="subcontractor")


@pytest.fixture
def plating(db):
    return Process.objects.create(code="PL", name="作り物のめっき", is_outsourced=True, sort_order=2)


@pytest.fixture
def customer_row(user, customer, item):
    return services.map_partner_item(customer, item, "X-1", user)


@pytest.fixture
def supplier_row(user, supplier, item):
    return services.add_partner_item({"partner": supplier, "item": item, "partner_part_no": "", "partner_item_name": ""}, user)


def _price(item, user, **values):
    data = {"item": item, "kind": ItemPrice.KIND_SALE, "unit_price": Decimal("1200"), **values}
    return services.add_item_price(data, user)


def _row(item, **values):
    return ItemPrice.objects.create(**{"item": item, "kind": ItemPrice.KIND_SALE, "unit_price": Decimal("1200"), **values})


def _saved_message(response):
    assert response.status_code == 204
    return json.loads(response["HX-Trigger"])["seizou:saved"]["message"]


# ---------------------------------------------------------------- 表


@pytest.mark.django_db
def test_rows_with_blank_fields_are_saved(item, customer_row):
    # (a) 数量の下限が空・適用開始日が空・共通 (取引先の行が空) の行を保存できる
    common = _row(item)
    assert (common.partner_item, common.min_qty, common.start_date) == (None, None, None)
    _row(item, partner_item=customer_row, min_qty=Decimal("100"), start_date=date(2026, 4, 1))
    assert ItemPrice.objects.count() == 2


@pytest.mark.django_db
def test_positive_constraints(item):
    # (b) 単価 0 と -1・数量の下限 0 は表の制約で止まる。数量の下限の空は通る (C98-3)
    for price in (Decimal("0"), Decimal("-1")):
        with pytest.raises(IntegrityError), transaction.atomic():
            _row(item, unit_price=price)
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(item, min_qty=Decimal("0"))
    _row(item, min_qty=None)


@pytest.mark.django_db
def test_same_band_constraint(item, customer_row, supplier_row, plating):
    # (c) 同じ条件の 2 つ目は、空の欄どうしも同じ値と見て止める (C98-2)
    _row(item)
    _row(item, partner_item=customer_row)
    _row(item, min_qty=Decimal("100"))
    _row(item, start_date=date(2026, 4, 1))
    for values in (
        {},
        {"partner_item": customer_row},
        {"min_qty": Decimal("100.00")},
        {"start_date": date(2026, 4, 1)},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            _row(item, **values)
    # 別の部品の共通の行・下限だけ違う・開始日だけ違う・売りと発注で違う・工程だけ違う行は通る
    other = Item.objects.create(part_no="T-200", name="作り物の板")
    _row(other)
    _row(item, min_qty=Decimal("200"))
    _row(item, start_date=date(2026, 10, 1))
    _row(item, kind=ItemPrice.KIND_PURCHASE)
    _row(item, kind=ItemPrice.KIND_PURCHASE, process=plating)
    _row(item, partner_item=supplier_row, kind=ItemPrice.KIND_PURCHASE)


def test_price_basis_text():
    # (d) 単価の後ろに添える単位の字
    assert price_basis_text("unit", "kg") == "kg"
    assert price_basis_text("unit", "") == "個"
    assert price_basis_text("lump", "kg") == "式"


# ---------------------------------------------------------------- services


@pytest.mark.django_db
def test_add_item_price(user, item, customer_row, plating, settings):
    # (e) 共通の行と取引先の行ができる。売りで渡した工程は空で保存する
    common = _price(item, user, process=plating)
    assert common.partner_item is None and common.process is None
    row = _price(item, user, partner_item=customer_row, min_qty=Decimal("100"), quote_no=" Q-1 ")
    assert row.partner_item == customer_row and row.quote_no == "Q-1"

    # 別の部品の取引先の行を渡すと断る (行は増えない)
    other = Item.objects.create(part_no="T-200", name="作り物の板")
    with pytest.raises(ValueError, match="選んだ取引先は、この部品の取引先のタブにありません"):
        _price(other, user, partner_item=customer_row)
    with pytest.raises(ValueError, match="知らない項目 \\(price\\)"):
        services.add_item_price({"item": item, "kind": "sale", "unit_price": Decimal("1"), "price": 1}, user)
    with pytest.raises(ValueError) as refused:
        _price(item, user, unit_price=Decimal("0"), min_qty=Decimal("5"))
    assert str(refused.value) == "保存できません。単価は 0 より大きい数にしてください。"
    with pytest.raises(ValueError) as refused:
        _price(item, user, min_qty=Decimal("0"))
    assert str(refused.value) == "保存できません。数量の下限は 0 より大きい数にしてください。数量によらないときは空にします。"

    # 同じ条件の 2 つ目は断る。売りで工程を渡した 2 つ目 (工程は空にそろう) も同じ条件
    with pytest.raises(ValueError) as refused:
        _price(item, user, unit_price=Decimal("999"))
    assert str(refused.value) == SAME_BAND
    with pytest.raises(ValueError) as refused:
        _price(item, user, process=plating)
    assert str(refused.value) == SAME_BAND
    assert ItemPrice.objects.count() == 2

    # 金額の閲覧が無い人には括弧の無い字 (¥ を出さない)
    settings.SEIZOU = MONEY_ON
    with pytest.raises(ValueError) as refused:
        _price(item, user)
    assert str(refused.value) == SAME_BAND_HIDDEN


@pytest.mark.django_db
def test_update_item_price(user, item, customer_row):
    # (f) 値が変わる。部品の鍵は断る。ほかの行と同じ条件に直すと断る。自分のまま単価だけ直すのは通る
    first = _price(item, user)
    second = _price(item, user, min_qty=Decimal("100"))
    services.update_item_price(second, {"unit_price": Decimal("1000"), "note": "改定前の単価"}, user)
    second.refresh_from_db()
    assert (second.unit_price, second.note) == (Decimal("1000"), "改定前の単価")
    with pytest.raises(ValueError, match="知らない項目 \\(item\\)"):
        services.update_item_price(second, {"item": item}, user)
    with pytest.raises(ValueError) as refused:
        services.update_item_price(second, {"min_qty": None}, user)
    assert str(refused.value) == SAME_BAND
    services.update_item_price(first, {"unit_price": Decimal("1300")}, user)
    first.refresh_from_db()
    assert first.unit_price == Decimal("1300")


@pytest.mark.django_db
def test_delete_item_price_and_partner_item(user, item, customer_row, supplier_row):
    # (g) 単価の行を消す。もう一度は断る。部品の取引先の行を消すと、その行の単価の行も消える (共通とほかの取引先の行は残る)
    row = _price(item, user, partner_item=customer_row)
    assert services.delete_item_price(row, user) is None
    with pytest.raises(ValueError) as refused:
        services.delete_item_price(row, user)
    assert str(refused.value) == "削除できません。この単価の行は既に消してあります。"

    _price(item, user, partner_item=customer_row)
    _price(item, user, partner_item=customer_row, min_qty=Decimal("100"))
    common = _price(item, user)
    other = _price(item, user, partner_item=supplier_row, kind=ItemPrice.KIND_PURCHASE)
    services.delete_partner_item(customer_row, user)
    assert set(ItemPrice.objects.values_list("pk", flat=True)) == {common.pk, other.pk}


@pytest.mark.django_db
def test_item_prices_order(user, item, customer_row, supplier_row, plating):
    # (h) 取引先の行が共通より先、同じ取引先では売りが先、適用開始日の新しい順で空は後、数量の下限の小さい順で空が先
    common = _price(item, user)
    late = _price(item, user, partner_item=customer_row, start_date=date(2026, 10, 1))
    blank_start = _price(item, user, partner_item=customer_row)
    early = _price(item, user, partner_item=customer_row, start_date=date(2026, 4, 1))
    banded = _price(item, user, partner_item=customer_row, min_qty=Decimal("100"))
    purchase = _price(item, user, partner_item=customer_row, kind=ItemPrice.KIND_PURCHASE)
    sub_blank = _price(item, user, partner_item=supplier_row, kind=ItemPrice.KIND_PURCHASE)
    sub_process = _price(item, user, partner_item=supplier_row, kind=ItemPrice.KIND_PURCHASE, process=plating)
    assert services.item_prices(item) == [late, early, blank_start, banded, purchase, sub_blank, sub_process, common]


@pytest.mark.django_db
def test_price_stays_when_blank_row_gets_part_no(user, item, supplier, supplier_row):
    # (i) 品番の空の取引先の行に単価の行を付けてから品番を結ぶと、単価の行は同じ取引先の行に付いたまま
    price = _price(item, user, partner_item=supplier_row, kind=ItemPrice.KIND_PURCHASE)
    mapped = services.map_partner_item(supplier, item, "B-2", user)
    assert mapped.pk == supplier_row.pk
    price.refresh_from_db()
    assert price.partner_item_id == supplier_row.pk


@pytest.mark.django_db
def test_quote_expired(item):
    # (j) 期限が昨日なら真・今日なら偽・空なら偽
    today = timezone.localdate()
    assert _row(item, quote_expires=today - timedelta(days=1)).quote_expired is True
    assert _row(item, min_qty=Decimal("2"), quote_expires=today).quote_expired is False
    assert _row(item, min_qty=Decimal("3")).quote_expired is False


# ---------------------------------------------------------------- 画面


def _form(item, **values):
    data = {
        "item": item.pk,
        "partner_item": "",
        "kind": "sale",
        "process": "",
        "min_qty": "",
        "unit_price": "1200",
        "price_basis": "unit",
        "start_date": "",
        "quote_no": "",
        "quote_date": "",
        "quote_expires": "",
        "note": "",
    }
    data.update({key: str(value) for key, value in values.items()})
    return data


def test_add_screen(client_logged_in, user, item, customer_row, supplier_row, plating):
    # (k) 取引先の選択肢はその部品の取引先の行と共通だけ。工程は協力企業に出す使用中の工程だけ
    Process.objects.create(code="CUT", name="作り物の切断", is_outsourced=False)
    other_item = Item.objects.create(part_no="T-200", name="作り物の板")
    other_row = services.map_partner_item(supplier_row.partner, other_item, "B-9", user)
    url = f'{reverse("masters:itemprice_add")}?item={item.pk}&partner_item={supplier_row.pk}&kind=purchase'
    response = client_logged_in.get(url, **MODAL)
    form = response.context["form"]
    assert set(form.fields["partner_item"].queryset) == {customer_row, supplier_row}
    assert other_row not in set(form.fields["partner_item"].queryset)
    html = response.content.decode()
    assert "共通 (どの取引先にも同じ単価)" in html and "作り物の協力企業 (品番なし)" in html
    assert list(form.fields["process"].queryset) == [plating]

    add = reverse("masters:itemprice_add")
    assert _saved_message(client_logged_in.post(add, _form(item, partner_item=supplier_row.pk, kind="purchase", process=plating.pk), **MODAL)) == "保存しました。"

    # 単価 0 は欄の下の字と赤の帯で止まる
    response = client_logged_in.post(add, _form(item, unit_price="0"), **MODAL)
    body = response.content.decode()
    assert response.status_code == 200
    assert "単価は 0 より大きい数にしてください。" in body and "alert-error" in body

    # 同じ条件の 2 つ目は C98-2 の字 (Django の制約の字でない)
    assert response.status_code == 200
    _saved_message(client_logged_in.post(add, _form(item), **MODAL))
    response = client_logged_in.post(add, _form(item, unit_price="999"), **MODAL)
    body = response.content.decode()
    assert response.status_code == 200
    assert "同じ条件の単価の行がもうあります (¥1,200 / 個)。" in body
    assert "制約 “" not in body


def test_detail_tab_shows_prices(client_logged_in, user, item, customer_row, supplier_row, plating):
    # (k) 部品の詳細の取引先のタブに単価の字が出る
    _price(item, user, partner_item=supplier_row, kind=ItemPrice.KIND_PURCHASE, process=plating)
    _price(
        item,
        user,
        min_qty=Decimal("100"),
        unit_price=Decimal("50000"),
        price_basis="lump",
        quote_no="Q-7",
        quote_expires=timezone.localdate() - timedelta(days=1),
    )
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    for word in ("¥1,200 / 個", "¥50,000 / 式", "100 個から", "数量によらない", "期限切れ", "共通 (どの取引先にも同じ単価)", "作り物のめっき", "協力企業への発注"):
        assert word in html, word
    assert html.count("期限切れ") == 1
    assert f'{reverse("masters:itemprice_add")}?item={item.pk}&amp;kind=sale&amp;next=' in html
    assert f'{reverse("masters:itemprice_add")}?item={item.pk}&amp;partner_item={supplier_row.pk}&amp;kind=purchase&amp;next=' in html
    assert f'{reverse("masters:itemprice_add")}?item={item.pk}&amp;partner_item={customer_row.pk}&amp;kind=sale&amp;next=' in html


def test_common_prices_without_partner_rows(client_logged_in, user, db):
    # 取引先の行が 0 件でも共通の単価の行があれば表を出し、その下に空の枠の箱を出す
    item = Item.objects.create(part_no="T-300", name="作り物の板", unit="")
    _price(item, user)
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    assert "共通 (どの取引先にも同じ単価)" in html and "¥1,200 / 個" in html
    assert "取引先が入ると、ここに並びます。" in html


def test_delete_screens(client_logged_in, user, item, customer_row):
    # (l) 取引先の行の削除の確かめに単価の行の件数。単価の行の削除の小窓に赤の「削除」と明細の金額の 1 文、POST で 204 と行が消える
    _price(item, user, partner_item=customer_row)
    price = _price(item, user, partner_item=customer_row, min_qty=Decimal("100"))
    html = client_logged_in.get(reverse("masters:partneritem_delete", args=[customer_row.pk]), **MODAL).content.decode()
    assert "この行の単価の行 2 件も一緒に消えます。" in html

    url = reverse("masters:itemprice_delete", args=[price.pk])
    html = client_logged_in.get(url, **MODAL).content.decode()
    assert 'class="btn btn-error"' in html and "今までの明細の金額は変わりません" in html
    assert "¥" not in html
    assert _saved_message(client_logged_in.post(url, {}, **MODAL)) == "削除しました。"
    assert not ItemPrice.objects.filter(pk=price.pk).exists()


def test_money_hidden(client_logged_in, user, item, customer_row, settings):
    # (m) 金額の閲覧が無い人 (money = on): 単価は「—」、単価の入口は出ない、単価の行の追加・削除は赤の断り、編集の画面に今の単価が無い
    price = _price(item, user, partner_item=customer_row, unit_price=Decimal("4321"))
    _price(item, user, partner_item=customer_row, min_qty=Decimal("100"), unit_price=Decimal("4000"))
    settings.SEIZOU = MONEY_ON
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    assert "¥" not in html
    assert "単価を足す" not in html and "共通の単価を足す" not in html
    assert reverse("masters:itemprice_update", args=[price.pk]) not in html
    assert reverse("masters:itemprice_delete", args=[price.pk]) not in html
    # 部品の取引先の行の「⋮」の「削除」は出る (C98-6 = a の後半)
    assert reverse("masters:partneritem_delete", args=[customer_row.pk]) in html

    response = client_logged_in.post(reverse("masters:itemprice_add"), _form(item, min_qty="5"), **MODAL)
    assert response.status_code == 200 and "単価表は金額が主な内容のため" in response.content.decode()
    html = client_logged_in.get(reverse("masters:itemprice_update", args=[price.pk]), **MODAL).content.decode()
    assert "4321" not in html

    refusal = "単価表は金額が主な内容のため、金額の閲覧の権限が無い方は単価の行を消せません。"
    url = reverse("masters:itemprice_delete", args=[price.pk])
    response = client_logged_in.post(url, {}, **MODAL)
    assert response.status_code == 200 and refusal in response.content.decode()
    response = client_logged_in.post(url, {})
    assert response.status_code == 200 and refusal in response.content.decode()
    assert ItemPrice.objects.filter(pk=price.pk).exists()

    # 同じ人の部品の取引先の行の削除は通り、単価の行も消える。確かめの黄の 1 行に金額は出ない
    url = reverse("masters:partneritem_delete", args=[customer_row.pk])
    html = client_logged_in.get(url, **MODAL).content.decode()
    assert "この行の単価の行 2 件も一緒に消えます。" in html
    warning = html[html.index("alert-warning") : html.index("</div>", html.index("alert-warning"))]
    assert "¥" not in warning
    assert _saved_message(client_logged_in.post(url, {}, **MODAL)) == "削除しました。"
    assert not PartnerItem.objects.filter(pk=customer_row.pk).exists()
    assert not ItemPrice.objects.exists()


def test_quotes(client_logged_in, user, item, customer_row):
    # (n) 取引先の行の「見積書を付ける」は添付ファイルの入力を、部品の取引先の種類・行の pk・備考「見積書」で開く
    from seizou.attachments import services as attachments_services

    content_type = ContentType.objects.get_for_model(PartnerItem)
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    assert f'{reverse("attachments:attachment_add")}?content_type={content_type.pk}&amp;object_id={customer_row.pk}&amp;note=見積書&amp;next=' in html
    attachment = attachments_services.add_attachment(
        customer_row, SimpleUploadedFile("作り物の見積書.pdf", b"%PDF-1.4\n"), user, kind="other", note="見積書"
    )
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    assert "作り物の見積書.pdf" in html
    response = client_logged_in.get(reverse("attachments:attachment_detail", args=[attachment.pk]))
    assert response.url == reverse("masters:partneritem_detail", args=[customer_row.pk])
    assert client_logged_in.get(response.url).url == reverse("masters:item_detail", args=[item.pk])
