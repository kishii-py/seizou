"""横断の検索の画面 (seizou.core.search) と、部品の search module の約束。"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.core import search as core_search
from seizou.core.search import SearchRow, SearchSection
from seizou.core.views import url_or_none
from seizou.masters import search as masters_search
from seizou.masters.models import Item, Partner, PartnerItem
from seizou.orders import search as orders_search
from seizou.orders import services as orders_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(db):
    return Partner.objects.create(code="C001", name="作り物の得意先", role="customer")


@pytest.fixture
def other_customer(db):
    return Partner.objects.create(code="C002", name="作り物の別の得意先", role="both")


@pytest.fixture
def bracket(db):
    return Item.objects.create(part_no="P-1001", name="ブラケット", drawing_no="DW-501", unit="個")


@pytest.fixture
def shaft(db):
    return Item.objects.create(part_no="P-2002", name="シャフト", drawing_no="DW-777", unit="本")


def make_order(user, customer, items, customer_order_no=""):
    lines = [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("100")} for item in items]
    return orders_services.create_order(
        customer, lines, user, order_date=date(2026, 11, 2), customer_order_no=customer_order_no
    )


def text(response) -> str:
    """HTML の tag を外した字 (span で分かれた「n 件中 a–b 件」などを 1 続きで見るため)。"""
    return re.sub(r"<[^>]+>", "", response.content.decode())


def section_kinds(response) -> list[str]:
    return [section["kind"] for section in response.context["sections"]]


def search_page(client, **params):
    return client.get(reverse("core:search"), params)


@pytest.mark.django_db
def test_search_requires_login(client):
    response = search_page(client, q="P-1001")
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_empty_query_searches_nothing_and_shows_hint(client_logged_in, bracket, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("空の言葉で探した")

    monkeypatch.setattr(masters_search, "search", must_not_run)
    response = search_page(client_logged_in, q="   ")
    assert response.status_code == 200
    body = response.content.decode()
    assert "キーワードを入れて検索してください" in body
    assert response.context["sections"] == []
    # 題と説明、chip (全て・部品マスター・受注) と得意先の選択。説明の 1 文は題の下に出さず (決め 11-03 14:1x)、
    # 目録と説明書が読む screen_help にある
    assert "<h1" in body and "検索</h1>" in body
    # 探す種類が分かる字 (初見テスト R5 の候補 C36 の 3。探す種類は providers() の 3 つで、取引先は探さない)
    assert "部品マスター・受注・マニュアルを横断して探し" in response.context["screen_help"]
    assert "全て" in body and "部品マスター" in body and "受注" in body
    assert "全得意先" in body


@pytest.mark.parametrize("word", ["P-1001", "ブラケ", "DW-501", "X-9"])
def test_item_found_by_part_no_name_drawing_and_partner_part_no(client_logged_in, bracket, shaft, customer, word):
    PartnerItem.objects.create(partner=customer, item=bracket, partner_part_no="X-9001")
    response = search_page(client_logged_in, q=word)
    assert section_kinds(response) == ["items"]
    body = response.content.decode()
    assert 'id="search-items"' in body and "部品マスター (1)" in text(response)
    detail = reverse("masters:item_detail", args=[bracket.pk])
    assert f'data-href="{detail}"' in body and f'href="{detail}"' in body
    assert "P-2002" not in body
    assert "1 件中 1–1 件" in text(response)


def test_partner_narrows_items(client_logged_in, bracket, shaft, customer, other_customer):
    PartnerItem.objects.create(partner=customer, item=bracket, partner_part_no="X-1")
    PartnerItem.objects.create(partner=other_customer, item=shaft, partner_part_no="X-2")
    response = search_page(client_logged_in, q="P-", partner=customer.pk)
    body = response.content.decode()
    assert "P-1001" in body and "P-2002" not in body
    assert f'<option value="{customer.pk}" selected>' in body
    # 取引先の品番も、選んだ取引先の物だけで探す
    assert masters_search.search("X-2", partner=customer.pk).total == 0
    assert masters_search.search("X-2", partner=other_customer.pk).total == 1


def test_unknown_partner_means_all_customers(client_logged_in, bracket):
    response = search_page(client_logged_in, q="P-1001", partner="abc")
    assert response.status_code == 200
    assert response.context["partner"] == ""
    assert "P-1001" in response.content.decode()


def test_kind_items_hides_orders(client_logged_in, user, bracket, customer):
    make_order(user, customer, [bracket])
    both = search_page(client_logged_in, q="P-1001")
    assert section_kinds(both) == ["items", "orders"]
    only_items = search_page(client_logged_in, q="P-1001", kind="items")
    assert section_kinds(only_items) == ["items"]
    assert 'id="search-orders"' not in only_items.content.decode()
    # 知らない種類は「全て」と同じ
    assert section_kinds(search_page(client_logged_in, q="P-1001", kind="nothing")) == ["items", "orders"]


def test_orders_found_by_order_no_customer_order_no_and_line_part_no(client_logged_in, user, bracket, shaft, customer):
    order = make_order(user, customer, [bracket, shaft], customer_order_no="PO-555")
    for word in (order.order_no, "PO-555", "P-2002", "シャフト"):
        response = search_page(client_logged_in, q=word, kind="orders")
        assert section_kinds(response) == ["orders"], word
        body = response.content.decode()
        assert order.order_no in body and "受注 (1)" in text(response)
        assert "P-1001 ほか 1 件" in body
        assert "2026-11-02" in body
    section = orders_search.search(order.order_no)
    # 受注の詳細の画面はまだ無いことがある。そのときは url が None で、画面は落ちない。
    assert section.rows[0].url == url_or_none("orders:order_detail", order.pk)
    assert section.rows[0].cells[1] == customer


def test_orders_result_shows_customer_order_no(client_logged_in, user, bracket, customer):
    """受注の結果の 3 つ目の列は「得意先の注文番号」(初見テスト R5 の V117。候補 C38 の 5)。探した番号が行に同じ字で出る。"""
    make_order(user, customer, [bracket], customer_order_no="PO-555")
    section = orders_search.search("PO-555")
    assert section.columns[2].label == "得意先の注文番号"
    assert section.rows[0].cells[2] == "PO-555"

    body = search_page(client_logged_in, q="PO-555", kind="orders").content.decode()
    thead = body[body.index('id="search-orders"') :]
    thead = thead[: thead.index("</thead>")]
    assert re.findall(r"<th [^>]*>([^<]+)</th>", thead) == ["受注番号", "得意先", "得意先の注文番号", "受注日", "品番", "状態"]


def test_partner_narrows_orders(client_logged_in, user, bracket, customer, other_customer):
    mine = make_order(user, customer, [bracket])
    theirs = make_order(user, other_customer, [bracket])
    response = search_page(client_logged_in, q="P-1001", kind="orders", partner=other_customer.pk)
    body = response.content.decode()
    assert theirs.order_no in body and mine.order_no not in body


def test_no_hits_message(client_logged_in, bracket):
    response = search_page(client_logged_in, q="存在しない言葉")
    assert response.context["sections"] == []
    assert "該当する物はありません。" in response.content.decode()


def test_more_than_limit_asks_to_narrow(client_logged_in):
    Item.objects.bulk_create(Item(part_no=f"S-{n:03d}", name="作り物の座金") for n in range(51))
    response = search_page(client_logged_in, q="座金", kind="items")
    body = response.content.decode()
    assert "51 件中 1–50 件。絞り込んでください" in text(response)
    assert body.count("data-href=") == 50


def test_part_without_tables_is_skipped(client_logged_in, bracket, monkeypatch):
    def broken(*args, **kwargs):
        raise DatabaseError("表がまだ無い")

    monkeypatch.setattr(orders_search, "search", broken)
    response = search_page(client_logged_in, q="P-1001")
    assert response.status_code == 200
    assert section_kinds(response) == ["items"]


def test_contract_shapes_and_numbers_stay_numbers():
    section = core_search.display_section(
        SearchSection(
            kind="t",
            label="試し",
            columns=("番号", core_search.SearchColumn("数", numeric=True)),
            rows=(SearchRow(cells=("A-1", Decimal("-3.50")), url=None),),
            total=1,
        )
    )
    number = section["rows"][0]["cells"][1]
    assert number["value"] == Decimal("-3.50") and number["numeric"] and number["negative"]
    assert section["columns"][0].label == "番号" and section["more"] is False


def topbar_placeholder(body: str) -> str:
    """上の帯の検索の欄 (aria-label="横断して検索" の input) の placeholder の字。"""
    tag = re.search(r'<input[^>]*aria-label="横断して検索"[^>]*>', body).group()
    return re.search(r'placeholder="([^"]*)"', tag).group(1)


def test_topbar_search_goes_to_search_page_and_menu_has_search(client_logged_in):
    body = client_logged_in.get(reverse("core:home")).content.decode()
    search_url = reverse("core:search")
    assert f'action="{search_url}"' in body
    # 上の帯の欄の案内は、マニュアルも探すと分かる字で、13 字を超えない (初見テスト R5 の候補 C36 の 1。V120)
    placeholder = topbar_placeholder(body)
    assert placeholder == "品番・品名・マニュアルなど"
    assert len(placeholder) <= 13
    assert f'href="{search_url}"' in body and ">検索</span>" in body


def test_topbar_placeholder_without_manuals_part_keeps_order_no(client_logged_in, monkeypatch):
    """部品 manuals が入っていない会社では、上の帯の欄の案内は今までの字のまま (マニュアルを探さないため。候補 C36 の 1)。

    本文の全体では「マニュアル」を探さない (左のメニューの入口と検索の画面の種類の札は、部品の名前を差し替えても出るため)。
    """
    from seizou.core import context_processors

    names = [name for name in context_processors.part_names() if name != "manuals"]
    monkeypatch.setattr(context_processors, "part_names", lambda: names)

    placeholder = topbar_placeholder(client_logged_in.get(reverse("core:home")).content.decode())
    assert placeholder == "品番・品名・注文番号で検索"
    assert "マニュアル" not in placeholder


def test_search_menu_entry_is_active_on_search_page(client_logged_in):
    body = search_page(client_logged_in).content.decode()
    search_url = reverse("core:search")
    # 左のメニューの入口の <a> の tag (属性の並びに頼らない)。今の画面の印は aria-current="page"。
    tags = re.findall(rf'<a[^>]*href="{re.escape(search_url)}"[^>]*>', body)
    assert any('aria-current="page"' in tag for tag in tags)


# --- 語の揺れ (全角と半角・ひらがなとカタカナ)。一覧の検索と同じ core の search_q で引く ---
# 全角のダッシュは U+FF0D (－)。U+2212 (−) と長音 (ー) は NFKC で "-" にならないので品番の試験に使わない。


@pytest.mark.parametrize("word", ["ぶらけっと", "ﾌﾞﾗｹｯﾄ", "Ｐ－１００１", "ＤＷ－５０１"])
def test_items_found_by_other_width_and_kana(client_logged_in, bracket, shaft, word):
    response = search_page(client_logged_in, q=word)
    assert section_kinds(response) == ["items"], word
    body = response.content.decode()
    assert reverse("masters:item_detail", args=[bracket.pk]) in body
    assert "P-2002" not in body


def test_partner_part_no_and_company_fields_fold_too(bracket, customer, monkeypatch):
    PartnerItem.objects.create(partner=customer, item=bracket, partner_part_no="X-9001")
    assert masters_search.search("Ｘ－９００１").total == 1
    Item.objects.create(part_no="P-3003", name="座金", unit="ダース")
    assert masters_search.search("だーす").total == 0
    monkeypatch.setattr(company_fields, "search_fields_for", lambda model: ["unit"])
    assert masters_search.search("だーす").total == 1
    assert masters_search.search("ﾀﾞｰｽ").rows[0].cells[0] == "P-3003"


def test_orders_found_by_other_width_and_kana(client_logged_in, user, bracket, shaft, customer):
    order = make_order(user, customer, [bracket, shaft], customer_order_no="PO-555")
    for word in ("しゃふと", "ｼｬﾌﾄ", "ＰＯ－５５５", "Ｐ－２００２"):
        response = search_page(client_logged_in, q=word, kind="orders")
        assert section_kinds(response) == ["orders"], word
        assert order.order_no in response.content.decode(), word
    assert orders_search.search("ぶらけっと").total == 1


def test_all_terms_must_match_even_with_variants(client_logged_in, bracket, shaft):
    assert section_kinds(search_page(client_logged_in, q="ぶらけっと Ｐ－１００１")) == ["items"]
    assert search_page(client_logged_in, q="ぶらけっと Ｐ－２００２").context["sections"] == []


def test_cross_search_agrees_with_list_search(client_logged_in, bracket, shaft):
    detail = reverse("masters:item_detail", args=[bracket.pk])
    for word in ("ぶらけっと", "Ｐ－１００１"):
        listed = client_logged_in.get(reverse("masters:item_list"), {"q": word}, HTTP_HX_REQUEST="true")
        listed_body = listed.content.decode()
        assert "P-1001" in listed_body and "P-2002" not in listed_body, word
        found_body = search_page(client_logged_in, q=word, kind="items").content.decode()
        assert detail in found_body and "P-2002" not in found_body, word


# --- 探した項目の 1 行 (初見テスト R5 の候補 C36 の 2。V120: 材質で部品が出た人が「ほかの部品が漏れていないか不安」と書いた) ---


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない。tests/test_core_ux_fixes.py と同じ)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def test_search_page_shows_searched_fields_in_provider_order(client_logged_in, bracket, no_registration):
    """言葉があれば、探した種類ごとの項目の見出しが providers() の順に並び、結果の上に「探した項目」の 1 行が出る。"""
    response = search_page(client_logged_in, q="P-1001")
    fields = response.context["searched_fields"]
    assert [scope["label"] for scope in fields] == [module.LABEL for module in core_search.providers()]
    by_label = {scope["label"]: scope["fields"] for scope in fields}
    assert by_label == {
        "部品マスター": "自社品番・品名・図面番号・取引先の品番",
        "受注": "受注番号・得意先の注文番号・自社品番・品名",
        "出荷": "出荷番号・納品書番号",
        "マニュアル": "題・本文",
    }
    body = response.content.decode()
    assert "探した項目" in body
    assert "部品マスター: 自社品番・品名・図面番号・取引先の品番 / 受注: " in body
    # 0 件の箱の上にも出る
    missing = search_page(client_logged_in, q="作り物でない")
    assert missing.context["sections"] == []
    assert "探した項目" in missing.content.decode()


def test_searched_fields_follow_the_selected_kind(client_logged_in, bracket, no_registration):
    """種類の札で 1 つに絞ったら、その種類の分だけ (探していない種類を書かない)。本文の種類の名前は札とメニューにもあるので見ない。"""
    response = search_page(client_logged_in, q="P-1001", kind="items")
    assert response.context["searched_fields"] == [{"label": "部品マスター", "fields": "自社品番・品名・図面番号・取引先の品番"}]


def test_searched_fields_hidden_without_query(client_logged_in):
    response = search_page(client_logged_in, q="   ")
    assert response.context["searched_fields"] == []
    assert "探した項目" not in response.content.decode()


def test_searched_fields_skip_modules_without_search_labels(monkeypatch):
    """search_labels() は約束の「持たなくてもよい物」。持たない部品は 1 行に出ない (探すのは今までどおり)。"""

    class Plain:
        KIND = "plain"
        LABEL = "作り物の種類"

        @staticmethod
        def search(query, *, partner=None, limit=50):
            return None

    assert core_search.searched_fields([Plain, orders_search]) == [
        {"label": "受注", "fields": "受注番号・得意先の注文番号・自社品番・品名"}
    ]


def test_items_search_labels_follow_company_fields(no_registration, monkeypatch):
    """会社ごとの項目は、company_lookups() が探す物の列の label だけ (拡張の表が無ければ探さないので書かない)。

    画面を通さない: 差し替えたまま検索の画面で探すと、search() が試験の DB に無い ext の lookup で絞るため。
    """
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "材質", "searchable": True}])
    # 試験の DB には拡張の表が無いので、company_lookups() は ext の lookup を外し、材質は探さない
    assert masters_search.company_lookups() == []
    assert masters_search.search_labels() == ["自社品番", "品名", "図面番号", "取引先の品番"]

    monkeypatch.setattr(masters_search, "company_lookups", lambda: ["ext__ext01"])
    assert masters_search.search_labels() == ["自社品番", "品名", "図面番号", "取引先の品番", "材質"]

    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "処理の種類", "searchable": True}])
    labels = masters_search.search_labels()
    assert labels[-1] == "処理の種類" and "材質" not in labels


def search_page_placeholder(body: str) -> str:
    """検索の画面の欄 (id="search-q" の input) の placeholder の字。"""
    tag = re.search(r'<input id="search-q"[^>]*>', body).group()
    return re.search(r'placeholder="([^"]*)"', tag).group(1)


def test_search_page_placeholder_matches_topbar(client_logged_in, monkeypatch):
    """検索の画面の欄の案内は、上の帯の欄と同じ字と同じ書き分け (初見テスト R5 の候補 C36 の 3)。"""
    body = search_page(client_logged_in).content.decode()
    assert search_page_placeholder(body) == topbar_placeholder(body) == "品番・品名・マニュアルなど"

    from seizou.core import context_processors

    names = [name for name in context_processors.part_names() if name != "manuals"]
    monkeypatch.setattr(context_processors, "part_names", lambda: names)
    placeholder = search_page_placeholder(search_page(client_logged_in).content.decode())
    assert placeholder == "品番・品名・図面番号・注文番号など"
    assert "マニュアル" not in placeholder
