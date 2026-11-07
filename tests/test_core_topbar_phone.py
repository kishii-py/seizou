"""phone の上の帯の検索の欄 (初見テスト R5 の候補 C36 の 6。決め C36-1 = a)。

幅が 640px より狭いとき、検索の欄を押している間だけ右の組 (日付・現品票を読む・☆・案内・使い方・利用者) を出さず、
欄を残りの幅いっぱいに広げる。CSS の class だけで作る (検索の form に peer、右の組に max-sm:peer-focus-within:hidden)。
隠れる・広がる動きは pytest では見られないので、ここでは決める class の字と並びを見る (動きは本体の画面の確かめで見る)。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

PHONE_HIDE = "max-sm:peer-focus-within:hidden"


@pytest.fixture
def client_logged_in(client, db):
    user = get_user_model().objects.create_user(username="tester", password="tests-only")
    client.force_login(user)
    return client


def header_of(body: str) -> str:
    start = body.index('<header class="seizou-header')
    return body[start : body.index("</header>", start)]


def classes(tag: str) -> list[str]:
    return re.search(r'class="([^"]*)"', tag).group(1).split()


def test_search_form_is_peer_and_right_group_hides_while_focused_on_phone(client_logged_in):
    header = header_of(client_logged_in.get(reverse("core:home")).content.decode())
    form = re.search(r'<form[^>]*role="search"[^>]*>', header)
    right = re.search(r'<div class="ml-auto[^"]*">', header)
    assert form and right
    assert "peer" in classes(form.group())
    # 右の組は form の後の兄弟 (peer の印は前にある兄弟だけに効く)
    assert form.start() < right.start()
    right_classes = classes(right.group())
    assert PHONE_HIDE in right_classes
    # 変えない物: 右の組の ml-auto (右へ寄せる)、欄の form の幅 (残りの幅いっぱい・sm 以上は今の上限)
    assert right_classes[0] == "ml-auto"
    assert {"min-w-0", "flex-1", "sm:max-w-md"} <= set(classes(form.group()))
    # 隠すのは右の組だけ。☰ (メニューを開く) と header は隠さない
    menu = re.search(r'<label[^>]*aria-label="メニューを開く"[^>]*>', header).group()
    assert PHONE_HIDE not in menu
    assert header.count(PHONE_HIDE) == 1


def test_reader_button_sits_inside_the_right_group(client_logged_in):
    """上の帯の「現品票を読む」(候補 C102) は右の組の中にあるので、右の組と一緒に隠れる (ボタンに同じ class は要らない)。"""
    header = header_of(client_logged_in.get(reverse("core:home")).content.decode())
    right = re.search(r'<div class="ml-auto[^"]*">', header)
    assert 'aria-label="現品票を読む"' in header
    assert right.start() < header.index('aria-label="現品票を読む"')
