"""業務マニュアルの版 (新しい版として保存・版の履歴・差分・この版に戻す)。"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.manuals import services
from seizou.manuals.diff import ADDED, CHANGED, REMOVED, SAME, DiffRow, diff_rows
from seizou.manuals.models import ManualVersion


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def _rows_with(html, color):
    """class に color の字 (丸ごとの class 名) を含む <tr> の開きのタグの並び (属性の順と、ほかの class に頼らない)。"""
    return re.findall(rf'<tr [^>]*class="(?:[^"]* )?{re.escape(color)}(?: [^"]*)?"[^>]*>', html)


@pytest.fixture
def manual(user):
    manual = services.create_manual(None, "段取り替えの手順", "steps", user)
    return services.update_manual(manual, {"body": "## 準備\n\n古い行\n消す行\n残る行"}, user)


def test_save_as_new_version_from_edit_form(client_logged_in, manual):
    url = reverse("manuals:manual_version_create", args=[manual.pk])
    edit = client_logged_in.get(reverse("manuals:manual_update", args=[manual.pk])).content.decode()
    assert f'formaction="{url}"' in edit and "新しい版として保存" in edit and "版のメモ" in edit
    response = client_logged_in.post(
        url,
        {"title": "段取り替えの手順", "format": "steps", "folder": "", "body": "## 準備\n\n新しい行", "note": "手順を直した"},
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("manuals:manual_detail", args=[manual.pk])
    manual.refresh_from_db()
    assert (manual.version, manual.body) == (2, "## 準備\n\n新しい行")
    version = ManualVersion.objects.get()
    assert (version.number, version.note) == (1, "手順を直した")
    assert "古い行" in version.body
    history = client_logged_in.get(reverse("manuals:manualversion_list", args=[manual.pk])).content.decode()
    assert "版 1" in history and "手順を直した" in history and "今の版 2" in history
    assert reverse("manuals:manualversion_detail", args=[version.pk]) in history
    assert reverse("manuals:manualversion_restore", args=[version.pk]) in history
    # 「保存」はメモを残さず、版も増やさない。
    client_logged_in.post(
        reverse("manuals:manual_update", args=[manual.pk]),
        {"title": "段取り替えの手順", "format": "steps", "folder": "", "body": "上書き", "note": "残らないメモ"},
    )
    manual.refresh_from_db()
    assert manual.version == 2 and ManualVersion.objects.count() == 1


def test_version_list_empty(client_logged_in, manual):
    html = client_logged_in.get(reverse("manuals:manualversion_list", args=[manual.pk])).content.decode()
    assert "版はまだありません。" in html


def test_version_list_has_current_row_first(client_logged_in, manual, user):
    """版の履歴の頭に今の版の行 (「—」で操作の無い行) を置き、メモは変える前の中身の版の行に出る (C10-1 = b)。"""
    services.save_as_version(manual, {"body": "## 準備\n\n新しい行"}, "手順を直した", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_list", args=[manual.pk])).content.decode()
    assert "<th class=\"font-medium\">メモ (次の版で変えたこと)</th>" in html
    rows = re.findall(r'<tr class="h-10">(.*?)</tr>', html, re.S)
    assert len(rows) == 2
    current, previous = rows
    assert "版 2 (今の中身)" in current and current.count("—") == 3 and "href=" not in current
    assert "版 1</td>" in previous and "手順を直した" in previous
    # 差分の URL は戻すの URL の頭と同じ字なので、href の引用符まで含めて数える。
    assert html.count(f'href="{reverse("manuals:manualversion_detail", args=[version.pk])}"') == 1
    assert html.count(f'href="{reverse("manuals:manualversion_restore", args=[version.pk])}"') == 1
    assert reverse("manuals:manualversion_detail", args=[version.pk]) in previous


def test_note_help_says_which_row_gets_the_note(client_logged_in, manual):
    html = client_logged_in.get(reverse("manuals:manual_update", args=[manual.pk])).content.decode()
    assert "版の履歴では、変える前の中身の版の行に出ます。" in html


def test_diff_rows_kinds():
    rows = diff_rows("同じ\n古い\n消える\n残る", "同じ\n新しい\n残る\n足した")
    assert rows == [
        DiffRow(SAME, 1, "同じ", 1, "同じ"),
        DiffRow(CHANGED, 2, "古い", 2, "新しい"),
        DiffRow(REMOVED, 3, "消える", None, None),
        DiffRow(SAME, 4, "残る", 3, "残る"),
        DiffRow(ADDED, None, None, 4, "足した"),
    ]
    assert diff_rows("", "") == []
    assert [r.kind for r in diff_rows("a", "")] == [REMOVED]


def test_diff_page_has_four_kinds(client_logged_in, manual, user):
    services.save_as_version(manual, {"body": "## 準備\n\n新しい行\n残る行\n足した行", "title": "新しい題"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    # 最初の違う行の <tr> には id と scroll-mt-22 も付くので、class に色の字を含む <tr> を数える。
    assert len(_rows_with(html, "bg-success/10")) == 1
    assert len(_rows_with(html, "bg-error/10")) == 1
    assert len(_rows_with(html, "bg-warning/10")) == 1
    assert "<tr>" in html and "残る行" in html
    assert "題: 段取り替えの手順 → 新しい題" in html
    assert reverse("manuals:manualversion_restore", args=[version.pk]) in html
    assert reverse("manuals:manualversion_list", args=[manual.pk]) in html


def test_diff_page_counts_and_first_link(client_logged_in, manual, user):
    """凡例に種類ごとの行の数と「最初の違う所へ」が出て、飛び先の id は最初の違う行 (変えた行) の <tr> に 1 つだけ。"""
    services.save_as_version(manual, {"body": "## 準備\n\n新しい行\n残る行\n足した行"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    assert "足した 1 行" in html and "消した 1 行" in html and "変えた 1 行" in html
    assert '<a class="link link-primary" href="#first-diff">最初の違う所へ</a>' in html
    assert html.count('id="first-diff"') == 1
    target = re.search(r'<tr [^>]*id="first-diff"[^>]*>', html).group(0)
    assert target in _rows_with(html, "bg-warning/10") and target in _rows_with(html, "scroll-mt-22")
    assert "本文に違いはありません。" not in html


def test_diff_page_restore_button_names_the_version(client_logged_in, manual, user):
    """差分の右上の戻すボタンは、矢印の絵の無い「版 N に戻す」(C10-2 = a)。「復元」の字は出さない。"""
    services.save_as_version(manual, {"body": "版 2 の本文"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    url = reverse("manuals:manualversion_restore", args=[version.pk])
    labels = re.findall(rf'<a [^>]*href="{re.escape(url)}"[^>]*>(.*?)</a>', html, re.S)
    assert labels == ["版 1 に戻す"]
    assert "復元" not in html


def test_diff_page_title_only_change_has_no_link(client_logged_in, manual, user):
    services.save_as_version(manual, {"title": "新しい題"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    assert "本文に違いはありません。" in html
    assert "最初の違う所へ" not in html and 'id="first-diff"' not in html
    assert "足した 0 行" in html and "消した 0 行" in html and "変えた 0 行" in html


def test_diff_page_both_bodies_empty_shows_one_notice(client_logged_in, manual, user):
    services.update_manual(manual, {"body": ""}, user)
    services.save_as_version(manual, {"title": "新しい題"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    assert "その版も今の中身も、本文が空です。" in html
    assert "本文に違いはありません。" not in html and "最初の違う所へ" not in html


def test_restore_confirm_then_restore(client_logged_in, manual, user):
    services.save_as_version(manual, {"body": "版 2 の本文"}, "", user)
    version = ManualVersion.objects.get(number=1)
    url = reverse("manuals:manualversion_restore", args=[version.pk])
    page = client_logged_in.get(url)
    assert page.status_code == 200
    html = page.content.decode()
    assert "版 1 (" in html and "今の中身は版として残ります" in html
    assert reverse("manuals:manualversion_list", args=[manual.pk]) in html
    response = client_logged_in.post(url, {})
    assert response.status_code == 302
    assert response["Location"] == reverse("manuals:manual_detail", args=[manual.pk])
    manual.refresh_from_db()
    assert manual.body == "## 準備\n\n古い行\n消す行\n残る行"
    assert manual.version == 3
    assert ManualVersion.objects.get(number=2).note == "版 1 に戻す前の中身"
