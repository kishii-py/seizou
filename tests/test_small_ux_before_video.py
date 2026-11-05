"""撮影の前の小さい直し 5 つ (決め 11-05 15:0x。N160) の試験。

1 = 「AI に質問」の丸いボタンと本文の下の余白、2 = 受信した注文の空の枠、3 = 生産管理ボードの行のメニューの見出しと線、
4 = 受信した注文の未処理の行の太字と点、5 = 読み取りの「読み取る」の回る印。作り物のデータだけを使う。
"""

from django.urls import reverse

from seizou.connect.models import Inbox

HX = {"HTTP_HX_REQUEST": "true"}
EMPTY_LINE = "取引先から注文が届くと、ここに並びます。"


# 2. 受信した注文の空の枠


def test_inbox_empty_says_orders_arrive_and_links_connections(logged_in):
    # 画面の全体・行の部分・2 秒ごとの取りに行き (after=0) の 3 つとも同じ枠 (取りに行きで前の枠に戻らない)。
    url = reverse("connect:inbox_list")
    whole = logged_in.get(url).content.decode()
    partial = logged_in.get(url, **HX).content.decode()
    poll = logged_in.get(url, {"after": "0"}, **HX).content.decode()
    link = f'href="{reverse("connect:connection_list")}"'

    for body in (whole, partial, poll):
        assert body.count(EMPTY_LINE) == 1
        assert "まだ 1 件もありません。" in body
        assert link in body
        assert "絞りの条件に" not in body
    # 行の部分と取りに行きは、次の取りに行く要素を入れ直す (取りに行きが続く)。
    for body in (partial, poll):
        assert 'id="inbox-poll"' in body and 'hx-swap-oob="true"' in body


def test_inbox_filtered_empty_keeps_core_sentence(logged_in):
    # 絞りで 0 件のときは core の「絞りの条件に合う物はありません。」のまま (取りに行きも同じ)。
    url = reverse("connect:inbox_list")
    rejected = {"f_status": Inbox.STATUS_REJECTED}
    whole = logged_in.get(url, rejected).content.decode()
    poll = logged_in.get(url, {"after": "0", **rejected}, **HX).content.decode()

    for body in (whole, poll):
        assert "絞りの条件に合う物はありません。" in body
        assert EMPTY_LINE not in body
