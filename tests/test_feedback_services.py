"""フィードバックの services (作る・直す・確認済み・送った・伏せ字・Issue の文・画面の目録) のテスト。

本物の API は呼ばない (autouse の fixture が assistant の _ask_claude を「呼ばれたら AssertionError」に差し替える)。
画面の目録の控えは試験の前後で捨てる。取引先の名前・品番・会社名は作り物。
"""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from seizou.assistant import services as assistant_services
from seizou.core.models import Company
from seizou.feedback import services
from seizou.feedback.models import Feedback
from seizou.masters import services as masters_services
from tests.test_assistant_services import refuse_api

BASE = {"kind": "bug", "screen": "orders:order_list", "title": "受注の一覧で並びが戻る", "problem": "並び替えた後に詳細から戻ると、並びが元に戻る。"}


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def fresh_catalog():
    services.screen_catalog.cache_clear()
    yield
    services.screen_catalog.cache_clear()


@pytest.fixture
def manager(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user("other", password="pass-for-tests-only")


def test_create_feedback_defaults(plain):
    row = services.create_feedback(dict(BASE), plain)

    assert Feedback.objects.count() == 1
    row.refresh_from_db()
    assert row.status == "draft"
    assert row.created_by == plain
    assert (row.input_tokens, row.output_tokens) == (0, 0)
    assert row.transcript == []
    assert (row.situation, row.wish, row.screens) == ("", "", "")
    assert row.get_screen_display() == "受注 (orders:order_list)"
    assert str(row) == "受注の一覧で並びが戻る"


@pytest.mark.parametrize(
    ("change", "word"),
    [
        pytest.param({"unknown": "a"}, "知らない項目", id="unknown-key"),
        pytest.param({"kind": "other"}, "種類は", id="bad-kind"),
        pytest.param({"title": "   "}, "題を入力", id="empty-title"),
        pytest.param({"title": "あ" * 101}, "100 字", id="long-title"),
        pytest.param({"problem": "  "}, "困りごとを入力", id="empty-problem"),
        pytest.param({"screen": "no:such"}, "画面の目録にありません", id="unknown-screen"),
        pytest.param({"screens": "orders:order_list no:such"}, "画面の目録に無い名前があります: no:such", id="unknown-screens"),
    ],
)
def test_create_feedback_refuses_bad_values(plain, change, word):
    with pytest.raises(ValueError) as caught:
        services.create_feedback({**BASE, **change}, plain)

    assert word in str(caught.value)
    assert Feedback.objects.count() == 0


def test_create_feedback_keeps_transcript_and_tokens(plain):
    row = services.create_feedback(
        dict(BASE),
        plain,
        transcript=[{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}, {"x": 1}, {"role": "system", "text": "c"}],
        input_tokens=12,
        output_tokens=34,
    )

    row.refresh_from_db()
    assert row.transcript == [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}]
    assert (row.input_tokens, row.output_tokens) == (12, 34)

    rounded = services.create_feedback(dict(BASE), None, input_tokens=-5, output_tokens="x")
    assert (rounded.input_tokens, rounded.output_tokens) == (0, 0)
    assert rounded.created_by is None


def test_normalize_screens(db):
    assert services.normalize_screens("orders:order_list, orders:order_list  masters:item_list") == "orders:order_list masters:item_list"
    assert services.normalize_screens("") == ""
    assert services.normalize_screens(None) == ""
    with pytest.raises(ValueError, match="画面の目録に無い名前"):
        services.normalize_screens("orders:order_list no:such")


def test_update_feedback_owner_or_manager(plain, other, manager):
    row = services.create_feedback(dict(BASE), plain, input_tokens=7, output_tokens=8)

    services.update_feedback(row, {"title": "直した題", "wish": "並びを覚えてほしい"}, plain)
    row.refresh_from_db()
    assert (row.title, row.wish, row.problem) == ("直した題", "並びを覚えてほしい", BASE["problem"])

    with pytest.raises(ValueError, match="書いた人"):
        services.update_feedback(row, {"title": "ほかの人の題"}, other)
    row.refresh_from_db()
    assert row.title == "直した題"

    services.update_feedback(row, {"title": "世話役が直した題"}, manager)
    services.mark_confirmed(row, manager)
    with pytest.raises(ValueError, match="書いた人"):
        services.update_feedback(row, {"title": "確認済みの後の題"}, plain)
    with pytest.raises(ValueError, match="知らない項目"):
        services.update_feedback(row, {"status": "draft"}, manager)

    row.refresh_from_db()
    assert row.title == "世話役が直した題"
    assert row.status == "confirmed"
    assert (row.input_tokens, row.output_tokens) == (7, 8)


def test_mark_confirmed_and_sent(plain, manager):
    row = services.create_feedback(dict(BASE), plain)

    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.mark_confirmed(row, plain)
    with pytest.raises(ValueError, match="アカウント管理の担当者だけ"):
        services.mark_sent(row, plain)

    services.mark_confirmed(row, manager)
    row.refresh_from_db()
    assert row.status == "confirmed"
    with pytest.raises(ValueError, match="下書きだけ"):
        services.mark_confirmed(row, manager)
    with pytest.raises(ValueError, match="http か https"):
        services.mark_sent(row, manager, sent_url="ftp://example.com/issues/1")

    services.mark_sent(row, manager, sent_url="https://example.com/issues/1")
    row.refresh_from_db()
    assert (row.status, row.sent_url) == ("sent", "https://example.com/issues/1")
    with pytest.raises(ValueError, match="送った状態です"):
        services.mark_sent(row, manager)

    direct = services.create_feedback(dict(BASE), plain)
    services.mark_sent(direct, manager)
    direct.refresh_from_db()
    assert (direct.status, direct.sent_url) == ("sent", "")


def test_mask_text_hides_partner_names_and_money(plain, monkeypatch, django_assert_num_queries):
    masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "short_name": "作り物", "role": "customer"}, plain)
    text = "作り物の得意先に 1,200 円で ¥300 の見積。作り物の箱。品番 WK-1001 を 10 個。"

    with django_assert_num_queries(1):
        masked = services.mask_text(text)

    assert masked == "〇〇に 〇〇で 〇〇 の見積。〇〇の箱。品番 WK-1001 を 10 個。"

    # 取引先を読めない会社 (masters が無い・表が読めない) でも、金額は伏せる。
    monkeypatch.setattr(services, "_partner_names", lambda: [])
    assert services.mask_text(text) == "作り物の得意先に 〇〇で 〇〇 の見積。作り物の箱。品番 WK-1001 を 10 個。"


def test_export_markdown_shape(plain):
    Company.objects.create(name="作り物の製作所")
    masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, plain)
    row = services.create_feedback(
        {
            "kind": "common",
            "screen": "orders:order_list",
            "title": "作り物の得意先の受注を並べたい",
            "problem": "作り物の得意先の 5,000 円の受注が探しにくい。",
            "situation": "朝の確認のとき。",
            "wish": "",
            "screens": "orders:order_list masters:item_list",
        },
        plain,
        transcript=[{"role": "user", "text": "会話の中の字"}],
    )

    text = services.export_markdown(row)

    assert text.startswith("# 〇〇の受注を並べたい\n")
    assert "- 種類: 皆の要望" in text
    assert "- 画面: 受注 (orders:order_list)" in text
    assert "- 影響する画面: orders:order_list masters:item_list" in text
    assert "- 作品の版: seizou " in text
    assert f"- 書いた日: {timezone.localdate(row.created_at).isoformat()}" in text
    assert "## 困りごと\n〇〇の 〇〇の受注が探しにくい。" in text
    assert "## 場面\n朝の確認のとき。" in text
    assert "## 望む形" not in text
    for absent in ("会話の中の字", "tester", "作り物の製作所", "作り物の得意先", "5,000"):
        assert absent not in text

    row.screen = ""
    row.screens = ""
    blank = services.export_markdown(row)
    assert "- 画面: 指定なし" in blank
    assert "影響する画面" not in blank


def test_screen_catalog_and_choices(db):
    catalog = services.screen_catalog()
    assert "orders:order_list" in catalog
    assert services.screen_catalog() is catalog

    choices = dict(services.screen_choices())
    assert choices["orders:order_list"] == "受注"
    assert services.screen_name("orders:order_list") == "受注"
    assert services.screen_name("no:such") == ""
    assert services.screen_name(None) == ""


def test_delete_feedback_draft_only_by_owner_or_manager(plain, other, manager):
    draft = services.create_feedback(dict(BASE), plain)
    assert services.can_delete(draft, plain)
    assert services.can_delete(draft, manager)
    assert not services.can_delete(draft, other)

    with pytest.raises(ValueError, match="書いた人かアカウント管理の担当者だけ"):
        services.delete_feedback(draft, other)
    assert Feedback.objects.filter(pk=draft.pk).exists()

    services.delete_feedback(draft, plain)
    assert not Feedback.objects.filter(pk=draft.pk).exists()
    with pytest.raises(ValueError, match="既にありません"):
        services.delete_feedback(draft, plain)

    confirmed = services.create_feedback(dict(BASE), plain)
    services.mark_confirmed(confirmed, manager)
    sent = services.create_feedback(dict(BASE), plain)
    services.mark_sent(sent, manager)
    for row, label in ((confirmed, "確認済み"), (sent, "送った")):
        assert not services.can_delete(row, plain)
        assert not services.can_delete(row, manager)
        for user in (plain, manager):
            with pytest.raises(ValueError) as caught:
                services.delete_feedback(row, user)
            assert "下書きだけ" in str(caught.value)
            assert f"今は {label}" in str(caught.value)
        assert Feedback.objects.filter(pk=row.pk).exists()

    theirs = services.create_feedback(dict(BASE), other)
    services.delete_feedback(theirs, manager)
    assert not Feedback.objects.filter(pk=theirs.pk).exists()


def test_list_filter_accepts_known_values_only():
    from django.http import QueryDict

    empty = {"status": "", "kind": ""}
    assert services.list_filter({"status": "draft", "kind": "bug"}) == {"status": "draft", "kind": "bug"}
    assert services.list_filter({"status": "x", "kind": ""}) == empty
    assert services.list_filter({}) == empty
    assert services.list_filter(None) == empty
    assert services.list_filter(QueryDict("status=sent&kind=common")) == {"status": "sent", "kind": "common"}


# ---- 画面の字に URL の名前を出さない (N77 の 9) ----


def test_screen_choices_have_no_url_names(db):
    choices = services.screen_choices()
    labels = [label for _url_name, label in choices]

    assert all(":" not in label for label in labels)
    assert all(label.strip() for label in labels)
    assert len(labels) == len(set(labels))  # 見出しが重ならない
    # 同じ名前の画面は、目録の順で 2 つ目から「名前 (2)」
    catalog = services.screen_catalog()
    names = [screen.name for screen in catalog.values()]
    twice = next(name for name in names if names.count(name) > 1)
    same = [label for url_name, label in choices if catalog[url_name].name == twice]
    assert same[:2] == [twice, f"{twice} (2)"]
    # 値 (保存する字) は URL の名前のまま
    assert [url_name for url_name, _label in choices] == list(catalog)


def test_normalize_screens_accepts_screen_names(db):
    assert services.normalize_screens("受注、注文残") == "orders:order_list orders:orderline_list"
    assert services.normalize_screens("受注 orders:order_list") == "orders:order_list"
    assert services.normalize_screens("注文残,受注\n受注") == "orders:orderline_list orders:order_list"

    # 名前に空白を含む画面は、1 つの名前として引ける (同じ名前の画面があれば目録の順に全部)
    catalog = services.screen_catalog()
    spaced = next(screen.name for screen in catalog.values() if " " in screen.name)
    expected = " ".join(url_name for url_name, screen in catalog.items() if screen.name == spaced)
    assert services.normalize_screens(spaced) == expected
    assert services.normalize_screens(f"受注、{spaced}") == f"orders:order_list {expected}"

    with pytest.raises(ValueError) as refused:
        services.normalize_screens("無い画面")
    message = str(refused.value)
    assert "画面の名前" in message and "無い画面" in message
    assert "list_screens" not in message and "url_name" not in message
