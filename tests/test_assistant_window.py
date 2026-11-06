"""AI チャットの窓を動かす・大きさを変える・広げる (N110。決め 11-04 14:3x) の試験。

見る所: 窓の帯に掴みの印 (data-assistant-grip) と「広げる」のボタンがあり、窓を動かす JS (assistant/assistant-window.js) が
画面に読み込まれ、その static のファイルがあること。鍵の無い会社でも同じ (帯は鍵の有無で変わらない)。
見ない所 (理由): 掴んで動かす・角で大きさを変える・広げる・localStorage に覚える・スマホの板 (ブラウザが要る。本体が画面で見る)。
"""

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

from tests import test_assistant_views

# AI チャットの窓の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# no_real_api = 本物の API を呼んだら落とす (autouse)。with_key・without_key = 鍵の有無。user・client_logged_in = 普通の利用者。
no_real_api = test_assistant_views.no_real_api
with_key = test_assistant_views.with_key
without_key = test_assistant_views.without_key
user = test_assistant_views.user
client_logged_in = test_assistant_views.client_logged_in

WINDOW_JS = "assistant/assistant-window.js"


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["with_key", "without_key"])
def test_window_has_grip_and_expand_button(key, request, client_logged_in):
    request.getfixturevalue(key)
    html = client_logged_in.get(reverse("core:home")).content.decode()

    assert html.count("data-assistant-window") == 1
    assert html.count("data-assistant-grip") == 1
    assert 'id="assistant-expand"' in html
    assert 'aria-label="広げる"' in html
    # 窓の初めの位置 (根の div。右下) は今のまま (決め C37-3 = a)。入口は左のメニューの「AI に質問」
    assert 'id="assistant-root" class="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-2 print:hidden"' in html


@pytest.mark.django_db
def test_window_script_is_loaded(with_key, client_logged_in, client):
    html = client_logged_in.get(reverse("core:home")).content.decode()

    assert f'src="/static/{WINDOW_JS}" defer' in html
    assert finders.find(WINDOW_JS)
    client.logout()
    assert WINDOW_JS not in client.get(reverse("core:login")).content.decode()
