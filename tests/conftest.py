"""全部の試験に共通の fixture。

選べる設定の画面の上書き (seizou.core.conf) は process の中に CACHE_SECONDS 秒の控えを持つ。試験の transaction は戻るが
控えは戻らないので、試験ごとに前と後で捨てる。前で捨てる = 試験の最初の読みが必ず表を読むので、問い合わせの数を比べる試験の数が
決まる (控えの寿命の切れ目が数える区間に入らない)。後で捨てる = 上書きを書いた試験の値が、次の 30 秒のほかのファイルの試験に漏れない。
"""

import pytest

from seizou.core import conf


@pytest.fixture(autouse=True)
def fresh_setting_cache():
    conf.reset_cache()
    yield
    conf.reset_cache()
