"""全部の試験に共通の fixture。

選べる設定の画面の上書き (seizou.core.conf) は process の中に CACHE_SECONDS 秒の控えを持つ。試験の transaction は戻るが
控えは戻らないので、試験ごとに前と後で捨てる。前で捨てる = 試験の最初の読みが必ず表を読むので、問い合わせの数を比べる試験の数が
決まる (控えの寿命の切れ目が数える区間に入らない)。後で捨てる = 上書きを書いた試験の値が、次の 30 秒のほかのファイルの試験に漏れない。
"""

import pytest
from django.contrib.auth import get_user_model

from seizou.core import conf
from seizou.core import calendar


@pytest.fixture(autouse=True)
def fresh_setting_cache():
    conf.reset_cache()
    calendar.reset_cache()
    yield
    conf.reset_cache()
    calendar.reset_cache()


@pytest.fixture
def user(db):
    """試験の利用者 (11-04 の試験のファイルが同じ字で書いていた物)。試験のファイルで同じ名前を書けば、そちらが勝つ。"""
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client
