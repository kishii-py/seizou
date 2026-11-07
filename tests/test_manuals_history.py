"""業務マニュアルの版 (新しい版として保存・版の履歴・差分・この版に戻す)。"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.manuals import services
from seizou.manuals.diff import ADDED, CHANGED, REMOVED, SAME, DiffRow, diff_rows
from seizou.manuals.markdown import LineMark, line_mark
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


DIFF_COLORS = ("bg-success/10", "bg-error/10", "bg-warning/10")


def _body_rows(html):
    """差分の表の本文 (<tbody>) の <tr> の開きのタグの並び。"""
    body = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.S).group(1)
    return re.findall(r"<tr(?: [^>]*)?>", body)


def _same_rows(html):
    """本文の <tr> のうち、class に色の字 (丸ごとの class 名) を含まない物 (同じ行)。"""
    return [tag for tag in _body_rows(html) if not any(_rows_with(tag, color) for color in DIFF_COLORS)]


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
    # 同じ行の <tr> にもスマホの幅の class が付くので、色の字を含まない本文の <tr> を数える。
    assert len(_same_rows(html)) >= 1 and "残る行" in html
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


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("## 見出し", LineMark("heading", "見出し", "##", " 見出し")),
        ("- [ ] 項目", LineMark("check", "チェック項目", "- [ ]", " 項目")),
        ("- [x] 項目", LineMark("check", "チェック項目", "- [x]", " 項目")),
        (":::注意", LineMark("box_start", "注意の枠の始まり", ":::注意", "")),
        (":::危険", LineMark("box_start", "危険の枠の始まり", ":::危険", "")),
        (":::", LineMark("box_end", "枠の終わり", ":::", "")),
        ("- 2026-11-03 [変更] 題 — 1 文", LineMark("event", "経緯", "-", " 2026-11-03 [変更] 題 — 1 文")),
        ("- 項目", LineMark("bullet", "箇条書き", "-", " 項目")),
    ],
)
def test_line_mark_kinds(line, expected):
    """差分の行の頭の目印 (C10-4 = a)。記号の部分と残りの字をつなぐと元の行になる。"""
    mark = line_mark(line)
    assert mark == expected
    assert mark.symbol + mark.rest == line


@pytest.mark.parametrize("line", ["地の文", "### 小見出し", ":::その他", "", "1. 番号の付いた並び", "![説明](image:1)", "> 説明"])
def test_line_mark_none_for_plain_lines(line):
    assert line_mark(line) is None


def test_diff_page_marks_format_symbols(client_logged_in, manual, user):
    """差分の字の欄の頭に目印の札を出し、記号は薄い字で残す。タグを除いた欄の字に本文の 1 行がそのまま並ぶ。"""
    services.update_manual(manual, {"body": "## 準備\n- [ ] 確かめる\n:::注意\n気を付ける\n:::\n地の文"}, user)
    services.save_as_version(manual, {"body": "## 準備\n- [ ] 確かめる\n:::注意\n気を付ける\n:::\n地の文\n足した文"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    cells = re.findall(r'<td class="[^"]*whitespace-pre-wrap[^"]*">(.*?)</td>', html, re.S)
    plain = [re.sub(r"<[^>]+>", "", cell) for cell in cells]
    assert "見出し## 準備" in plain
    assert "チェック項目- [ ] 確かめる" in plain
    assert "注意の枠の始まり:::注意" in plain
    assert "枠の終わり:::" in plain
    assert '<span class="badge badge-sm badge-ghost mr-2 align-middle">見出し</span><span class="text-base-content/50">##</span> 準備' in html
    assert "地の文" in plain and "気を付ける" in plain
    assert all("badge" not in cell for cell, text in zip(cells, plain) if text in ("地の文", "気を付ける", "足した文"))
    assert "行の頭の目印は、本文の書式の記号の意味です。" in html


def test_diff_page_one_column_on_phone(client_logged_in, manual, user):
    """640px 未満は 1 列 (C10-3 = a)。差分の 1 行 = 1 つの <tr> のまま、幅の class で欄の見せ方を変える。

    見た目は app.css を作り直してから変わる。ここでは HTML の形 (行の数・id・見出し・class の字) だけを見る。
    """
    services.save_as_version(manual, {"body": "## 準備\n\n新しい行\n残る行\n足した行"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    assert len(_body_rows(html)) == len(diff_rows(version.body, "## 準備\n\n新しい行\n残る行\n足した行")) == 6
    assert html.count('id="first-diff"') == 1
    head = re.sub(r"<[^>]+>", "", re.search(r"<thead[^>]*>(.*?)</thead>", html, re.S).group(1))
    assert "版 1 と今の中身" in head
    assert re.search(r'<table class="[^"]*\bsm:table-fixed\b[^"]*">', html)
    assert all("max-sm:grid" in tag for tag in _body_rows(html))
    # 変えた行: 版の側 (赤の地・札「版 1」) の下に今の側 (黄の地・札「今の中身」)。
    changed = re.search(r'<tr [^>]*class="bg-warning/10 [^"]*"[^>]*>(.*?)</tr>', html, re.S).group(1)
    cells = re.findall(r'<td class="([^"]*)">(.*?)</td>', changed, re.S)
    assert [("max-sm:bg-error/10" in c, "max-sm:bg-warning/10" in c) for c, _ in cells] == [
        (True, False),
        (True, False),
        (False, True),
        (False, True),
    ]
    assert re.sub(r"<[^>]+>", "", cells[1][1]) == "版 1古い行"
    assert re.sub(r"<[^>]+>", "", cells[3][1]) == "今の中身新しい行"
    # 同じ行は今の中身の側だけ、消した行は版の側だけを出す。
    removed = re.search(r'<tr [^>]*class="bg-error/10 [^"]*"[^>]*>(.*?)</tr>', html, re.S).group(1)
    assert [("max-sm:hidden" in c) for c, _ in re.findall(r'<td class="([^"]*)">(.*?)</td>', removed, re.S)] == [
        False,
        False,
        True,
        True,
    ]
    same = re.search(r'<tr class="max-sm:grid max-sm:grid-cols-\[3rem_1fr\]">(.*?)</tr>', html, re.S).group(1)
    assert [("max-sm:hidden" in c) for c, _ in re.findall(r'<td class="([^"]*)">(.*?)</td>', same, re.S)] == [
        True,
        True,
        False,
        False,
    ]


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
    assert [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)] == [
        ("success", "版 1 の中身に戻しました。戻す前の中身は版 2 として残しました。")
    ]
    assert response["Location"] == reverse("manuals:manual_detail", args=[manual.pk])
    manual.refresh_from_db()
    assert manual.body == "## 準備\n\n古い行\n消す行\n残る行"
    assert manual.version == 3
    assert ManualVersion.objects.get(number=2).note == "版 1 に戻す前の中身"


# ---------------------------------------------------------------- 題の上のパンくず (初見テスト R5 の候補 C82 の 4・5)


def _crumb(html):
    """題の上のパンくずの nav (tests/test_detail_header.py の _crumb と同じ拾い方)。"""
    return re.search(r'<nav aria-label="パンくず"[^>]*>.*?</nav>', html, re.S).group(0)


def test_version_list_crumb_above_heading(client_logged_in, manual):
    """版の履歴の題の上に「マニュアル › 題 › 版の履歴」を出し、右上の詳細へ戻るボタンは出さない。"""
    html = client_logged_in.get(reverse("manuals:manualversion_list", args=[manual.pk])).content.decode()
    nav = _crumb(html)
    assert f'href="{reverse("manuals:manual_list")}">マニュアル</a>' in nav
    assert f'href="{reverse("manuals:manual_detail", args=[manual.pk])}">段取り替えの手順</a>' in nav
    assert 'aria-current="page">版の履歴</span>' in nav
    assert html.index('<nav aria-label="パンくず"') < html.index('<h1 class="text-3xl font-bold tracking-tight">版の履歴</h1>')
    assert "詳細を見る</a>" not in html


def test_diff_page_crumb_names_list_detail_and_history(client_logged_in, manual, user):
    """差分の view は土台の一覧の URL を引けないので、テンプレートが URL の名前で 4 段を書く。右上の戻すボタンは今どおり。"""
    services.save_as_version(manual, {"body": "版 2 の本文"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    nav = _crumb(html)
    parts = [
        f'href="{reverse("manuals:manual_list")}">マニュアル</a>',
        f'href="{reverse("manuals:manual_detail", args=[manual.pk])}">段取り替えの手順</a>',
        f'href="{reverse("manuals:manualversion_list", args=[manual.pk])}">版の履歴</a>',
        'aria-current="page">差分</span>',
    ]
    places = [nav.index(part) for part in parts]
    assert places == sorted(places)
    assert html.index('<nav aria-label="パンくず"') < html.index('<h1 class="text-3xl font-bold tracking-tight">差分</h1>')
    url = reverse("manuals:manualversion_restore", args=[version.pk])
    labels = re.findall(rf'<a [^>]*href="{re.escape(url)}"[^>]*>(.*?)</a>', html, re.S)
    assert labels == ["版 1 に戻す"]


def test_diff_page_crumb_shows_current_title(client_logged_in, manual, user):
    """題だけを変えた版の差分でも、パンくずのマニュアルの字は今の題。"""
    services.save_as_version(manual, {"title": "新しい題"}, "", user)
    version = ManualVersion.objects.get()
    html = client_logged_in.get(reverse("manuals:manualversion_detail", args=[version.pk])).content.decode()
    nav = _crumb(html)
    assert f'href="{reverse("manuals:manual_detail", args=[manual.pk])}">新しい題</a>' in nav
