"""初見テストと E2E (11-02) で見つかった食い違いの直し。直した物ごとに test client で確かめる。

番号は直しの一覧の番号 (1〜25 と、支払の条件を 2 列に分けた直し)。ブラウザでの見た目と動き (Enter・戻る・toast の重なり) は
朝の E2E で見る。ここでは、それを決める応答の見出しとテンプレートの字を見る。
"""

import re
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.views import defaults

import seizou.core
from seizou.core import fields as company_fields
from seizou.core import urls as core_urls
from seizou.core.views import BaseListView
from seizou.masters.models import Item, ItemProcess, Partner, PartnerItem, Process

FAVICON = Path(seizou.core.__file__).resolve().parent / "static" / "favicon.svg"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def partner(db):
    return Partner.objects.create(code="C001", name="作り物の得意先", short_name="作り得", role="customer")


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def text(response) -> str:
    return response.content.decode()


# ---------------------------------------------------------------- 1・2 一覧の戻る


@pytest.mark.django_db
def test_list_history_restore_returns_whole_page_with_vary(client_logged_in):
    Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    url = reverse("masters:item_list")
    rows = client_logged_in.get(url, {"sort": "-part_no"}, HTTP_HX_REQUEST="true")
    assert "<html" not in text(rows)  # 並び替えの htmx には行の部分だけ
    restored = client_logged_in.get(
        url, {"sort": "-part_no"}, HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true"
    )
    body = text(restored)
    # 戻る (履歴の復元) には、上の帯と左のメニューを持つ画面の全体
    assert "<html" in body and 'role="search"' in body and "seizou-drawer" in body
    for response in (rows, restored):
        assert "HX-Request" in response["Vary"]
    assert "HX-Request" in client_logged_in.get(url)["Vary"]


@pytest.mark.django_db
def test_list_restore_keeps_search_and_page_size(client_logged_in):
    Item.objects.create(part_no="P-2002", name="作り物のシャフト")
    restored = client_logged_in.get(
        reverse("masters:item_list"),
        {"q": "シャフト", "per": "20"},
        HTTP_HX_REQUEST="true",
        HTTP_HX_HISTORY_RESTORE_REQUEST="true",
    )
    body = text(restored)
    assert 'value="シャフト"' in body
    assert '<option value="20" selected>' in body
    # 一覧は htmx の履歴の写しを残さず、戻るは毎回サーバーに聞き直す
    assert 'hx-history="false"' in body


# ---------------------------------------------------------------- 3・4・5 入力


@pytest.mark.django_db
def test_form_has_enter_guard_script(client_logged_in):
    body = text(client_logged_in.get(reverse("masters:partner_create")))
    assert "data-enter-guard" in body
    assert 'addEventListener("keydown"' in body and "TEXTAREA" in body and "isComposing" in body


@pytest.mark.django_db
def test_file_must_be_chosen_again_after_error(client_logged_in):
    url = reverse("masters:item_create")
    page = text(client_logged_in.get(url))
    assert "入れた値は、止まったときも消えません (ファイルは選び直します)。" in page
    assert "ファイルは選び直してください" not in page
    drawing = SimpleUploadedFile("drawing.png", b"\x89PNG\r\n", content_type="image/png")
    response = client_logged_in.post(url, {"part_no": "P-3003", "drawing": drawing})  # 品名が空で止まる
    assert response.status_code == 200
    assert "ファイルは選び直してください" in text(response)
    assert not Item.objects.exists()


@pytest.mark.django_db
def test_form_without_file_keeps_plain_sentence(client_logged_in):
    body = text(client_logged_in.get(reverse("masters:partner_create")))
    assert "入れた値は、止まったときも消えません。" in body
    assert "ファイルは選び直します" not in body


@pytest.mark.django_db
def test_decimal_field_is_text_with_decimal_keyboard(client_logged_in):
    body = text(client_logged_in.get(reverse("masters:item_create")))
    start = body.index('name="standard_price"')
    tag = body[body.rindex("<input", 0, start) : body.index(">", start)]
    assert 'type="text"' in tag and 'inputmode="decimal"' in tag
    assert "[&_input[inputmode=decimal]]:text-right" in body  # 右寄せは残す


@pytest.mark.django_db
def test_decimal_field_rejects_letters_in_japanese(client_logged_in):
    response = client_logged_in.post(
        reverse("masters:item_create"), {"part_no": "P-4004", "name": "作り物の板", "standard_price": "12e"}
    )
    assert response.status_code == 200
    assert "数値を入力してください" in text(response)
    assert 'value="12e"' in text(response)  # 打った字は消さない
    assert not Item.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("typed", ["1,200", "１２００", "１，２００"])
def test_decimal_field_accepts_commas_and_full_width(client_logged_in, typed):
    response = client_logged_in.post(
        reverse("masters:item_create"), {"part_no": "P-5005", "name": "作り物の軸", "standard_price": typed}
    )
    assert response.status_code == 302
    assert Item.objects.get().standard_price == Decimal("1200")


@pytest.mark.django_db
@pytest.mark.parametrize(("typed", "message"), [("0", "1 以上"), ("32", "31 以下")])
def test_closing_day_has_min_max_and_same_messages(client_logged_in, typed, message):
    url = reverse("masters:partner_create")
    page = text(client_logged_in.get(url))
    start = page.index('name="closing_day"')
    tag = page[page.rindex("<input", 0, start) : page.index(">", start)]
    assert 'type="text"' in tag and 'inputmode="numeric"' in tag and 'type="number"' not in tag
    response = client_logged_in.post(url, {"code": "C009", "name": "作り物の社", "role": "customer", "closing_day": typed})
    assert response.status_code == 200
    assert message in text(response)
    assert not Partner.objects.exists()


# ---------------------------------------------------------------- 6 toast


@pytest.mark.django_db
def test_toasts_let_clicks_through_and_sit_at_the_top_right(client_logged_in):
    # 11-04: 右上に置き、箱の地を 80% の濃さにして下を透かす (本人の決め)。枠も箱も押下を下へ通し、閉じるボタンだけが受ける。
    response = client_logged_in.post(
        reverse("masters:item_create"), {"part_no": "P-6006", "name": "作り物の蓋"}, follow=True
    )
    body = text(response)
    start = body.index('id="seizou-toasts"')
    frame = body[body.rindex("<div", 0, start) : body.index(">", start)]
    assert "pointer-events-none" in frame and "toast-top" in frame and "toast-end" in frame
    assert "toast-bottom" not in frame
    assert 'class="seizou-toast pointer-events-none' in body
    assert "btn-xs pointer-events-auto" in body  # 閉じるボタン
    # 見た目の形 (主色の地は不透明・緑の印・下の線)。地を透かすのをやめたのは、下の画面の字が最後の行に重なって見えたため
    assert "rounded-xl bg-primary p-4" in body and "text-success" in body and "seizou-toast-bar" in body


# ---------------------------------------------------------------- 7 図面のファイル


def test_media_needs_login_even_in_debug(settings, tmp_path, client, db):
    # 11-03: 開発機でも /media/ の道はログインを見る (seizou.core.media。前は DEBUG のとき誰にでも返した)。
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "drawings").mkdir()
    (tmp_path / "drawings" / "a.png").write_bytes(b"\x89PNG\r\n")
    response = client.get("/media/drawings/a.png")
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_media_is_served_to_logged_in_user_without_runserver(settings, tmp_path, monkeypatch, client_logged_in):
    # 本番の形 (DEBUG が偽で runserver でない) でも同じ道が返す。配り方の既定は Django が送る。
    settings.DEBUG = False
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr(sys, "argv", ["gunicorn"])
    (tmp_path / "drawings").mkdir()
    (tmp_path / "drawings" / "a.png").write_bytes(b"\x89PNG\r\n")
    response = client_logged_in.get("/media/drawings/a.png")
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"\x89PNG\r\n"


def test_media_route_is_absent_for_external_media_url(settings):
    settings.MEDIA_URL = "https://files.example.com/"
    assert core_urls.file_patterns() == []


# ---------------------------------------------------------------- 8 小窓を新しいタブで開く


@pytest.mark.django_db
def test_modal_without_htmx_redirects_to_detail(client_logged_in):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    url = reverse("masters:item_modal", args=[item.pk])
    response = client_logged_in.get(url)
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:item_detail", args=[item.pk])
    assert client_logged_in.get(url, HTTP_HX_REQUEST="true").status_code == 200


# ---------------------------------------------------------------- 9 ログアウトの後の戻る


@pytest.mark.django_db
def test_screens_are_not_stored(client_logged_in):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    for url in (
        reverse("core:home"),
        reverse("masters:item_list"),
        reverse("masters:item_detail", args=[item.pk]),
        reverse("masters:item_create"),
    ):
        assert "no-store" in client_logged_in.get(url)["Cache-Control"], url


# ---------------------------------------------------------------- 10 誤りの画面


@pytest.mark.django_db
def test_not_found_page_is_japanese(client):
    response = client.get("/no-such-screen/")
    assert response.status_code == 404
    body = text(response)
    assert "ページが見つかりません" in body and "最初の画面へ" in body
    assert "Not Found" not in body


def test_server_error_page_is_japanese_without_request_context(rf):
    response = defaults.server_error(rf.get("/"))
    assert response.status_code == 500
    assert "エラーが起きました" in text(response) and "最初の画面へ" in text(response)


@pytest.mark.django_db
def test_permission_denied_page_is_japanese(rf):
    request = rf.get("/")
    request.user = get_user_model()()
    response = defaults.permission_denied(request, PermissionDenied())
    assert response.status_code == 403
    assert "この操作はできません" in text(response)


@pytest.mark.django_db
def test_csrf_failure_page_is_japanese():
    response = Client(enforce_csrf_checks=True).post(reverse("core:login"), {"username": "x", "password": "y"})
    assert response.status_code == 403
    assert "画面を開き直してもう一度" in text(response) and "最初の画面へ" in text(response)


# ---------------------------------------------------------------- 11 favicon


@pytest.mark.django_db
def test_favicon_link_and_file(client):
    body = text(client.get(reverse("core:login")))
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in body
    assert FAVICON.read_text(encoding="utf-8").startswith("<svg")


# ---------------------------------------------------------------- 12 空の選択肢


@pytest.mark.django_db
def test_empty_choice_is_japanese(client_logged_in):
    body = text(client_logged_in.get(reverse("masters:partner_create")))
    assert '<option value="" selected>選んでください</option>' in body
    assert "Select an option" not in body


# ---------------------------------------------------------------- 13 読める名前


@pytest.mark.django_db
def test_master_rows_have_readable_names(partner):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    link = PartnerItem.objects.create(partner=partner, item=item, partner_part_no="X-1", partner_item_name="相手の軸")
    process = Process.objects.create(code="P10", name="旋削")
    step = ItemProcess.objects.create(item=item, process=process, seq=1)
    assert str(link) == "作り得 X-1"
    partner.short_name = ""
    assert str(PartnerItem(partner=partner, item=item, partner_part_no="X-2")) == "作り物の得意先 X-2"
    assert str(process) == "P10 旋削"
    assert str(step) == "P-1001 P10 旋削"


@pytest.mark.django_db
def test_detail_tab_shows_partner_item_name(client_logged_in, partner):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    PartnerItem.objects.create(partner=partner, item=item, partner_part_no="X-1")
    body = text(client_logged_in.get(reverse("masters:item_detail", args=[item.pk])))
    # 取引先ごとの品番は表で出す (取引先は小窓を開く link、品番は別の枡)。str() の既定の "PartnerItem object" は出ない
    assert "X-1" in body and reverse("masters:partner_modal", args=[partner.pk]) in body
    assert "PartnerItem object" not in body


# ---------------------------------------------------------------- 14 空のタブ


@pytest.mark.django_db
def test_empty_tab_names_what_will_come(client_logged_in):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    body = text(client_logged_in.get(reverse("masters:item_detail", args=[item.pk])))
    assert "取引先が入ると、ここに並びます。" in body
    assert "標準の工程が入ると、ここに並びます。" in body
    assert "関係する物が入ると" not in body


# ---------------------------------------------------------------- 15 小窓の取引先ごとの品番と会社ごとの項目


@pytest.mark.django_db
def test_item_modal_lists_partner_part_numbers(client_logged_in, partner):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    url = reverse("masters:item_modal", args=[item.pk])
    empty = text(client_logged_in.get(url, HTTP_HX_REQUEST="true"))
    assert "取引先はまだありません。" in empty
    PartnerItem.objects.create(partner=partner, item=item, partner_part_no="X-1", partner_item_name="相手の軸")
    body = text(client_logged_in.get(url, HTTP_HX_REQUEST="true"))
    for word in ("取引先", "取引先の品番", "取引先の品名", "作り得", "X-1", "相手の軸"):
        assert word in body, word


@pytest.mark.django_db
def test_item_modal_shows_company_fields(client_logged_in, no_registration, monkeypatch):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    url = reverse("masters:item_modal", args=[item.pk])
    assert "作り物の材質" not in text(client_logged_in.get(url, HTTP_HX_REQUEST="true"))  # 登録が無い会社では出さない
    company_fields.register(
        "masters.Item",
        "fields.ItemExt",
        [{"label": "材質の印", "in_list": True}, {"label": "一覧に出さない項目", "in_list": False}],
    )
    monkeypatch.setattr(company_fields, "ext_value", lambda obj, name: "作り物の材質" if name == "ext01" else "x")
    body = text(client_logged_in.get(url, HTTP_HX_REQUEST="true"))
    assert "材質の印" in body and "作り物の材質" in body
    assert "一覧に出さない項目" not in body  # 一覧の列と同じ取り方 (in_list の物だけ)


# ---------------------------------------------------------------- 16 検索の案内


@pytest.mark.django_db
def test_search_hint_has_company_searchable_fields(client_logged_in, no_registration):
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "材質", "searchable": True, "in_list": False}])
    body = text(client_logged_in.get(reverse("masters:item_list")))
    assert 'placeholder="自社品番・品名・図面番号・材質で絞り込む"' in body


@pytest.mark.django_db
def test_search_hint_wraps_under_keyword_label_on_phone(client_logged_in, no_registration):
    """幅が 640px より狭いときだけ出す要素 (sm:hidden) に、欄の案内と同じ字を折り返して出す (初見テスト R5 の候補 C36 の 5)。

    phone では欄の幅で案内が切れ、項目の名前まで隠れる一覧があった (b-flagged-11)。placeholder にも同じ字があるので、
    本文の全体では探さず、sm:hidden の付いた要素の中の字で見る。placeholder は今のまま。
    """
    body = text(client_logged_in.get(reverse("masters:item_list")))
    phone_hints = re.findall(r'<p class="[^"]*\bsm:hidden\b[^"]*">([^<]*)</p>', body)
    assert phone_hints == ["自社品番・品名・図面番号で絞り込む"]
    assert 'placeholder="自社品番・品名・図面番号で絞り込む"' in body
    # 見出し「キーワード」の下、欄の前に置く
    assert body.index(">キーワード</label>") < body.index("自社品番・品名・図面番号で絞り込む</p>") < body.index('id="list-q"')

    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "材質", "searchable": True, "in_list": False}])
    body = text(client_logged_in.get(reverse("masters:item_list")))
    assert re.findall(r'<p class="[^"]*\bsm:hidden\b[^"]*">([^<]*)</p>', body) == ["自社品番・品名・図面番号・材質で絞り込む"]


# ---------------------------------------------------------------- 17 合計の行


class _TotalItemList(BaseListView):
    model = Item
    columns = ("part_no", {"name": "standard_price", "label": "単価の合計", "total": True})
    template_name = "core/list.html"


@pytest.mark.django_db
def test_master_lists_have_no_total_row(client_logged_in, partner):
    Item.objects.create(part_no="P-1001", name="作り物の軸", standard_price=Decimal("100"))
    partner.closing_day = 31
    partner.save()
    for url in (reverse("masters:item_list"), reverse("masters:partner_list")):
        body = text(client_logged_in.get(url))
        assert "<tfoot>" not in body and "合計" not in body, url


@pytest.mark.django_db
def test_column_can_ask_for_a_total(user):
    Item.objects.create(part_no="P-1001", name="作り物の軸", standard_price=Decimal("100"))
    Item.objects.create(part_no="P-1002", name="作り物の板", standard_price=Decimal("20.50"))
    request = RequestFactory().get("/")
    request.user = user
    response = _TotalItemList.as_view()(request)
    response.render()
    body = text(response)
    # 合計の値の小数の桁は DB による (SQLite は 120.5、PostgreSQL は 120.50)
    assert "<tfoot>" in body and "合計" in body and "120.5" in body and "単価の合計" in body


# ---------------------------------------------------------------- 18 CSV の名前


@pytest.mark.django_db
def test_csv_file_name_is_screen_name_and_date(client_logged_in):
    response = client_logged_in.get(reverse("masters:item_list"), {"format": "csv"})
    today = timezone.localdate().strftime("%Y%m%d")
    header = response["Content-Disposition"]
    assert header.startswith("attachment;")
    assert f'filename="item_{today}.csv"' in header  # ASCII だけの代わり
    assert f"filename*=UTF-8''{quote(f'部品マスター_{today}.csv', safe='')}" in header


# ---------------------------------------------------------------- 19〜23 言葉


@pytest.mark.django_db
def test_words_use_parts_and_new_labels(client_logged_in, partner):
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    home = text(client_logged_in.get(reverse("core:home")))
    assert "部品マスターにある部品の数" in home and "品目" not in home
    assert "出荷の残りがある明細" in home and "注残の明細" not in home
    assert "受信した注文" in home and "受信箱の未処理" not in home
    detail = text(client_logged_in.get(reverse("masters:item_detail", args=[item.pk])))
    assert "部品の詳細" in detail and "標準単価</dt>" in detail and "標準単価 (円)</dt>" not in detail
    listing = client_logged_in.get(reverse("masters:item_list"))
    # 説明の 1 文は題の下に出さず (決め 11-03 14:1x)、目録と説明書が読む screen_help にある
    assert "品名を押すと、画面を移らずに中身を見られます。" in listing.context["screen_help"]
    assert "小窓で開けます" not in text(listing) and "小窓で開けます" not in listing.context["screen_help"]
    form = text(client_logged_in.get(reverse("masters:partner_create")))
    for label in ("金額の端数の扱い", "消費税の端数の扱い", "消費税の端数の向き", "取引先の種類", "支払の月"):
        assert label in form, label
    assert "小計の計算方法" not in form and "消費税の計算方法" not in form


# ---------------------------------------------------------------- 25 と支払の条件


@pytest.mark.django_db
def test_closing_and_payment_days_read_as_words(client_logged_in, partner):
    partner.closing_day = 31
    partner.payment_month = Partner.PAYMENT_NEXT
    partner.payment_day = 25
    partner.save()
    other = Partner.objects.create(
        code="C002", name="作り物の別の得意先", role="customer", closing_day=20, payment_month="next", payment_day=31
    )
    detail = text(client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk])))
    assert "月末" in detail and "翌月 25 日" in detail
    other_detail = text(client_logged_in.get(reverse("masters:partner_detail", args=[other.pk])))
    assert "20 日" in other_detail and "翌月末" in other_detail
    listing = text(client_logged_in.get(reverse("masters:partner_list")))
    for word in ("月末", "20 日", "翌月 25 日", "翌月末"):
        assert word in listing, word
    # 表の値は数のまま
    partner.refresh_from_db()
    assert (partner.closing_day, partner.payment_day) == (31, 25)


def test_payment_day_words_without_month_or_day():
    assert Partner(payment_day=31).get_payment_day_display() == "月末"
    assert Partner(payment_day=10).get_payment_day_display() == "10 日"
    assert Partner(payment_month="after_next").get_payment_day_display() == "翌々月"
    assert Partner(payment_month="current", payment_day=31).get_payment_day_display() == "当月末"
    assert Partner().get_payment_day_display() is None
    assert Partner().get_closing_day_display() is None


@pytest.mark.django_db
def test_day_fields_show_guidance(client_logged_in):
    body = text(client_logged_in.get(reverse("masters:partner_create")))
    assert "1〜31。月末締めは 31" in body and "1〜31。月末は 31" in body


# ---------------------------------------------------------------- 言葉を一般的な業務システムの表現に (11-02 19:0x)


@pytest.mark.django_db
def test_buttons_and_titles_use_general_words(client_logged_in):
    listing = text(client_logged_in.get(reverse("masters:item_list")))
    assert "新規登録" in listing and "「新規登録」から 1 件目を登録します。" in listing
    assert "新しく入れる" not in listing
    create = text(client_logged_in.get(reverse("masters:item_create")))
    assert "部品の新規登録" in create and "キャンセル" in create and "やめる" not in create
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    detail = text(client_logged_in.get(reverse("masters:item_detail", args=[item.pk])))
    assert 'aria-label="パンくず"' in detail and "編集" in detail and "一覧へ</a>" not in detail and "直す</a>" not in detail
    update = text(client_logged_in.get(reverse("masters:item_update", args=[item.pk])))
    assert "部品の編集" in update
    modal = text(client_logged_in.get(reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true"))
    assert "詳細を見る" in modal and "詳細を開く" not in modal
    home = text(client_logged_in.get(reverse("core:home")))
    assert "ダッシュボード" in home and "はじめに" not in home


# ---------------------------------------------------------------- 初見テスト 5 回目 (R5) の候補 C32: 支払の 2 欄・詳細の二重・端数の既定

PAYMENT_MONTH_HELP = "翌月末払いなら「翌月」と支払日「31」、翌月 25 日払いなら「翌月」と支払日「25」"


def _field_box(body, name):
    """入力のカードの欄の箱 (欄の見出しの label の手前の、箱の div の開きの tag から label まで) の字。"""
    label = body.index(f'for="id_{name}"')
    return body[body.rindex('<div class="min-w-0', 0, label) : label]


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["masters:partner_create", "masters:partner_update"])
def test_payment_month_starts_a_row_beside_payment_day(client_logged_in, partner, url_name):
    """支払の月の欄だけを行の頭から始め、支払日と同じ行の左右に並べる (V42)。締日の右は空く。"""
    args = [partner.pk] if url_name == "masters:partner_update" else []
    body = text(client_logged_in.get(reverse(url_name, args=args)))
    assert body.count("sm:col-start-1") == 1
    assert "sm:col-start-1" in _field_box(body, "payment_month")
    for name in ("closing_day", "payment_day", "rounding"):
        assert "sm:col-start-1" not in _field_box(body, name), name
    # 1 列 (幅 640 未満) の並びは今と同じ 締日 → 支払の月 → 支払日
    assert body.index('for="id_closing_day"') < body.index('for="id_payment_month"') < body.index('for="id_payment_day"')


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["masters:item_create", "masters:process_create"])
def test_other_forms_have_no_row_start(client_logged_in, url_name):
    """row_start_fields を持たない form の入力のカードには、行の頭の class が付かない。"""
    body = text(client_logged_in.get(reverse(url_name)))
    assert 'class="min-w-0' in body
    assert "sm:col-start-1" not in body


@pytest.mark.django_db
def test_payment_month_help_explains_the_pair(client_logged_in):
    """支払の月の案内に、支払日との組み合わせの例を書く。支払日の案内は今の字のまま。"""
    body = text(client_logged_in.get(reverse("masters:partner_create")))
    assert PAYMENT_MONTH_HELP in body
    assert "1〜31。月末は 31" in body


PARTNER_MODAL_LABELS = [
    "取引先コード", "名前", "略称", "取引先の種類", "締日", "支払日", "金額の端数の扱い", "消費税の端数の扱い",
]


@pytest.mark.django_db
def test_detail_and_modal_show_payment_terms_once(client_logged_in, partner):
    """詳細と小窓に「支払の月」の行を出さず、支払日の行の字 (支払の月を含む) だけで見せる (V98)。"""
    partner.payment_month = Partner.PAYMENT_NEXT
    partner.payment_day = 25
    partner.save()
    detail = client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk]))
    labels = [row["label"] for row in detail.context["fields"]]
    values = [row["value"] for row in detail.context["fields"]]
    assert "支払の月" not in labels and "支払日" in labels
    assert values.count("翌月 25 日") == 1
    modal = client_logged_in.get(reverse("masters:partner_modal", args=[partner.pk]), HTTP_HX_REQUEST="true")
    labels = [row["label"] for row in modal.context["fields"]]
    values = [row["value"] for row in modal.context["fields"]]
    assert "支払の月" not in labels
    assert labels[:8] == PARTNER_MODAL_LABELS
    assert labels[7] == "消費税の端数の扱い"
    assert values.count("翌月 25 日") == 1


@pytest.mark.django_db
def test_detail_keeps_payment_month_when_day_is_empty(client_logged_in, partner):
    """支払日が空で支払の月だけあるときも、支払日の行が月の字を出す (行を外しても情報は減らない)。"""
    partner.payment_month = Partner.PAYMENT_AFTER_NEXT
    partner.save()
    detail = client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk]))
    rows = {row["label"]: row["value"] for row in detail.context["fields"]}
    assert rows["支払日"] == "翌々月"
    assert "支払の月" not in rows


@pytest.mark.django_db
def test_rounding_help_shows_company_default_now(client_logged_in):
    """端数の 2 欄の案内に、会社の既定の今の値を出す (V119)。"""
    from seizou.core.models import Company

    Company.objects.create(name="作り物の会社", rounding="floor", tax_rounding="ceil")
    body = text(client_logged_in.get(reverse("masters:partner_create")))
    assert "空なら会社の既定 (今は 切捨) に従います。" in body
    assert "空なら会社の既定 (今は トータル) に従います。" in body
    assert "空なら会社の既定 (今は 切上) に従います。" in body


@pytest.mark.django_db
def test_partner_form_reads_company_without_creating_it():
    """form を作るときは会社の行を読むだけ。行が無ければ表の既定の字 (四捨五入・トータル) で、行を作らない。"""
    from seizou.core.models import Company
    from seizou.masters.forms import PartnerForm

    assert Company.objects.count() == 0
    fields = PartnerForm().fields
    assert "空なら会社の既定 (今は 四捨五入) に従います。" in fields["rounding"].help_text
    assert "空なら会社の既定 (今は トータル) に従います。" in fields["tax_mode"].help_text
    # 消費税の端数の向きは、会社の行が無ければ表の既定の切捨 (決め C95-1b)
    assert "空なら会社の既定 (今は 切捨) に従います。" in fields["tax_rounding"].help_text
    assert Company.objects.count() == 0


def _rows(response):
    return {row["label"]: row["value"] for row in response.context["fields"]}


@pytest.mark.django_db
def test_detail_and_modal_show_company_default_for_empty_rounding(client_logged_in, partner):
    """端数の扱いが空の取引先は「会社の既定 (四捨五入)」と出す (R4 の U118)。値があれば選択肢の字。"""
    detail = _rows(client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk])))
    assert detail["金額の端数の扱い"] == "会社の既定 (四捨五入)"
    assert detail["消費税の端数の扱い"] == "会社の既定 (トータル)"
    assert detail["消費税の端数の向き"] == "会社の既定 (切捨)"
    modal = _rows(client_logged_in.get(reverse("masters:partner_modal", args=[partner.pk]), HTTP_HX_REQUEST="true"))
    assert modal["金額の端数の扱い"] == "会社の既定 (四捨五入)"
    assert modal["消費税の端数の扱い"] == "会社の既定 (トータル)"
    # 消費税の端数の向きは 9 つ目の項目で、小窓 (先頭の 8 項目) には出ないので、詳細だけで見る
    partner.rounding = "ceil"
    partner.tax_mode = "total"
    partner.tax_rounding = "round"
    partner.save()
    detail = _rows(client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk])))
    assert detail["金額の端数の扱い"] == "切上"
    assert detail["消費税の端数の扱い"] == "トータル"
    assert detail["消費税の端数の向き"] == "四捨五入"
    # 表の値はそのまま
    partner.refresh_from_db()
    assert (partner.rounding, partner.tax_mode, partner.tax_rounding) == ("ceil", "total", "round")


@pytest.mark.django_db
def test_detail_follows_company_default_value(client_logged_in, partner):
    """会社の行の端数の扱いを変えると、空の取引先の字もその値になる。"""
    from seizou.core.models import Company

    Company.objects.create(name="作り物の会社", rounding="floor", tax_rounding="round")
    detail = _rows(client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk])))
    assert detail["金額の端数の扱い"] == "会社の既定 (切捨)"
    assert detail["消費税の端数の扱い"] == "会社の既定 (トータル)"
    assert detail["消費税の端数の向き"] == "会社の既定 (四捨五入)"
