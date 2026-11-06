"""AI (Claude の API) の呼び方の共通の部分。assistant・intake・feedback が使う。

- 鍵は、呼ぶ側が名前を渡す環境変数からだけ読み、client に明示して渡す (ほかの資格に落ちる道を作らない)。
  鍵の値を画面・ログ・例外の文・返す物に出さない。
- SDK (anthropic) は関数の中で import する (SDK が無くても、この module の import は落ちない)。
- 待ちの上限は呼ぶ側が渡す。再試行はしない。
- SDK の例外は、呼ぶ側が渡した表の日本語の文で ValueError に替える。文は部品ごとに違うので、この module は文を持たない。
- API を呼ぶ関数 (部品ごとの _ask_claude) は部品に残す。試験はその関数を作り物の返事に差し替える。
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from django.db import DatabaseError

from seizou.core import conf
from seizou.core import services as core_services


def company_name() -> str:
    """system に入れる会社名。会社の行が読めないとき (DatabaseError) は "seizou"。"""
    try:
        return core_services.company().name
    except DatabaseError:
        return "seizou"


def has_key(api_key_env) -> bool:
    """鍵の環境変数 api_key_env に空でない値があるか。値そのものは返さない。"""
    return bool(os.environ.get(api_key_env, "").strip())


def model_for(setting_key, default) -> str:
    """使う model の名前。会社の設定 setting_key (core の conf.DEFAULTS の鍵) が空でない字なら前後の空白を落とした字、ほかは default。"""
    value = conf.setting(setting_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def client(api_key_env, timeout):
    """環境変数 api_key_env の鍵で SDK の client を作って返す。待ちは timeout 秒で、再試行しない。

    鍵の環境変数が無ければ KeyError (呼ぶ側が先に鍵の有無を見る)。
    """
    import anthropic

    return anthropic.Anthropic(api_key=os.environ[api_key_env], timeout=timeout, max_retries=0)


@contextmanager
def translate_errors(messages):
    """with の中で起きた SDK の例外を、messages の文の ValueError に替える。ほかの例外はそのまま通す。

    messages の鍵は 5 つ。bad_key (鍵が合わない)・busy (混んでいる)・no_credit (状態 402 = 残高が足りない)・
    unreachable (届かない。402 の外の状態の番号は「文 (状態 番号)」にする)・timeout (待ちの上限を超えた)。
    元の例外は from None で切る (返事の本文と鍵の値を文に入れない)。
    """
    import anthropic

    # 順に意味がある。SDK では AuthenticationError と RateLimitError が APIStatusError の子、
    # APITimeoutError が APIConnectionError の子なので、子を先に見る。
    try:
        yield
    except anthropic.AuthenticationError:
        raise ValueError(messages["bad_key"]) from None
    except anthropic.RateLimitError:
        raise ValueError(messages["busy"]) from None
    except anthropic.APIStatusError as exc:
        if exc.status_code == 402:
            raise ValueError(messages["no_credit"]) from None
        raise ValueError(f"{messages['unreachable']} (状態 {exc.status_code})") from None
    except anthropic.APITimeoutError:
        raise ValueError(messages["timeout"]) from None
    except anthropic.APIConnectionError:
        raise ValueError(messages["unreachable"]) from None
