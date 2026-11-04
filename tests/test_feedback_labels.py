"""フィードバックの「影響する画面」の欄の見出し (精査 A7 の 1-2 の不具合 4 の残り。依頼書 N100)。

入力の画面 (新規登録と編集) と詳細の見出しに、表の verbose_name の内部の字を出さない。表と migration は変えない。
本物の API は呼ばない (autouse の fixture が assistant の _ask_claude を「呼ばれたら AssertionError」に差し替える)。題は作り物。
"""

import pytest
from django.contrib.auth import get_user_model

from seizou.assistant import services as assistant_services
from seizou.feedback import services
from seizou.feedback.models import Feedback
from tests.test_assistant_services import refuse_api

#: 表の verbose_name の括弧の中の字 (入力の画面と詳細に出さない)
INTERNAL = "(URL の名前。空白で区切る)"


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def fresh_catalog():
    services.screen_catalog.cache_clear()
    yield
    services.screen_catalog.cache_clear()


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def test_screens_label_has_no_url_name_words(client, plain):
    row = services.create_feedback(
        {"kind": "bug", "screen": "orders:order_list", "title": "受注の一覧で並びが戻る", "problem": "並び替えた後に戻ると、並びが元に戻る。", "screens": "受注、生産管理ボード"},
        plain,
    )
    client.force_login(plain)
    # 表の verbose_name は今のまま (migration を作らない)
    assert Feedback._meta.get_field("screens").verbose_name == "影響する画面 " + INTERNAL

    for url in ("/feedback/new/", f"/feedback/{row.pk}/edit/"):
        html = client.get(url).content.decode()
        assert INTERNAL not in html, url
        assert html.count('class="font-medium">影響する画面</label>') == 1, url

    html = client.get(f"/feedback/{row.pk}/").content.decode()
    assert INTERNAL not in html
    assert html.count('<dt class="text-base-content/70">影響する画面</dt>') == 1
    # 替えるのは影響する画面の見出しだけ (ほかの見出しは表の verbose_name のまま)
    assert '<dt class="text-base-content/70">題</dt>' in html
    assert '<dt class="text-base-content/70">困りごと</dt>' in html
