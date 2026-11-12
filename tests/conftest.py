"""全部の試験に共通の fixture。

選べる設定の画面の上書き (seizou.core.conf) は process の中に CACHE_SECONDS 秒の控えを持つ。試験の transaction は戻るが
控えは戻らないので、試験ごとに前と後で捨てる。前で捨てる = 試験の最初の読みが必ず表を読むので、問い合わせの数を比べる試験の数が
決まる (控えの寿命の切れ目が数える区間に入らない)。後で捨てる = 上書きを書いた試験の値が、次の 30 秒のほかのファイルの試験に漏れない。
Django の ContentType の控え (process の中に残る) も、試験ごとに前で捨てる (11-12 の 504 回目に足した)。捨てないと、添付ファイルの枠のある
詳細の問い合わせの数が、同じ process で先に回った試験が控えを温めたかで 1 つ変わる (tests/test_core_screen_queries.py が、
全部を回すと通り、1 本だけ回すと落ちた)。試験ごとに空にした形で全部を回し、ほかに数の変わる試験が無い事を見た。

置き場の一覧 (seizou.site_registry。seizou new と seizou ai-docs が登録する) の置き場 SEIZOU_HOME は、全部の試験で一時のフォルダにする
(本物の ~/.seizou/sites.json に書かない)。module の fixture で seizou new を回す試験より先に効くよう、session の fixture にする。

AI の呼び出しの上限 (seizou.core.ai) の回数の数 (一時の置き場のファイル) と同時の数の semaphore も、試験ごとに前と後で捨てる
(1 回の pytest の中で同じ利用者の数を貯めて、上限で断られないように)。
"""

import pytest
from django.contrib.auth import get_user_model

from seizou.core import ai
from seizou.core import conf
from seizou.core import calendar


@pytest.fixture(autouse=True, scope="session")
def seizou_home(tmp_path_factory):
    """置き場の一覧の置き場 (環境変数 SEIZOU_HOME) を一時のフォルダにする。子の process にも環境変数で渡る。"""
    home = tmp_path_factory.mktemp("seizou-home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SEIZOU_HOME", str(home))
        yield home


@pytest.fixture(autouse=True)
def fresh_setting_cache():
    from django.contrib.contenttypes.models import ContentType  # apps が読み込まれた後に import する

    conf.reset_cache()
    calendar.reset_cache()
    ContentType.objects.clear_cache()
    yield
    conf.reset_cache()
    calendar.reset_cache()


@pytest.fixture(autouse=True)
def fresh_ai_limits():
    ai.reset_limits()
    yield
    ai.reset_limits()


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
