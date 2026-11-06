"""seizou.core.ai (AI の呼び方の共通の部分) の試験。

本物の API は呼ばない。SDK (anthropic) は、例外の親子を本物に合わせた作り物の module に差し替える。
鍵の値は作り物の字だけを使う。
"""

import importlib
import sys
from types import ModuleType

import pytest

import seizou.core
from seizou.core import ai, conf

#: 試験だけで使う環境変数の名前 (本物の鍵の環境変数に触らない)
KEY_ENV = "SEIZOU_TEST_AI_KEY"
#: 作り物の鍵の字
FAKE_KEY = "作り物の鍵"

#: 作り物の文の表 (部品の本物の文とは別の字)
MESSAGES = {
    "bad_key": "作り物: 鍵が合わない",
    "busy": "作り物: 混んでいる",
    "no_credit": "作り物: 残高が足りない",
    "unreachable": "作り物: 届かない",
    "timeout": "作り物: 待ちの上限",
}


def fake_anthropic():
    """作り物の anthropic の module。例外の親子は本物の SDK と同じ。Anthropic は渡された引数を控える。"""
    module = ModuleType("anthropic")

    class APIError(Exception):
        pass

    class APIStatusError(APIError):
        def __init__(self, status_code):
            super().__init__(f"作り物の状態 {status_code}")
            self.status_code = status_code

    class AuthenticationError(APIStatusError):
        def __init__(self):
            super().__init__(401)

    class RateLimitError(APIStatusError):
        def __init__(self):
            super().__init__(429)

    class APIConnectionError(APIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class Anthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    module.APIError = APIError
    module.APIStatusError = APIStatusError
    module.AuthenticationError = AuthenticationError
    module.RateLimitError = RateLimitError
    module.APIConnectionError = APIConnectionError
    module.APITimeoutError = APITimeoutError
    module.Anthropic = Anthropic
    return module


@pytest.fixture
def sdk(monkeypatch):
    module = fake_anthropic()
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


# ---- has_key と model_for ----


def test_has_key_follows_env(monkeypatch):
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)
    assert ai.has_key(KEY_ENV) is True
    monkeypatch.delenv(KEY_ENV)
    assert ai.has_key(KEY_ENV) is False
    monkeypatch.setenv(KEY_ENV, "   ")
    assert ai.has_key(KEY_ENV) is False


def test_model_for_reads_setting_then_default(settings):
    settings.SEIZOU = {}
    assert ai.model_for("assistant_model", "作り物の既定") == conf.DEFAULTS["assistant_model"]
    settings.SEIZOU = {"assistant_model": " 作り物の model "}
    assert ai.model_for("assistant_model", "作り物の既定") == "作り物の model"
    settings.SEIZOU = {"assistant_model": ""}
    assert ai.model_for("assistant_model", "作り物の既定") == "作り物の既定"
    settings.SEIZOU = {"assistant_model": 5}
    assert ai.model_for("assistant_model", "作り物の既定") == "作り物の既定"


# ---- client ----


def test_import_does_not_need_sdk(monkeypatch):
    """SDK が無い環境でも、import の時点では落ちない (SDK は client の中で import する)。"""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    # 読み直した module が試験の後に残らないよう、親の package の属性も元に戻す。
    monkeypatch.delitem(sys.modules, "seizou.core.ai", raising=False)
    monkeypatch.delattr(seizou.core, "ai", raising=False)
    module = importlib.import_module("seizou.core.ai")

    monkeypatch.setenv(KEY_ENV, FAKE_KEY)
    with pytest.raises(ImportError):
        module.client(KEY_ENV, 60)


def test_client_passes_key_from_env_without_retry(sdk, monkeypatch):
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)

    made = ai.client(KEY_ENV, 7)

    assert isinstance(made, sdk.Anthropic)
    assert made.kwargs == {"api_key": FAKE_KEY, "timeout": 7, "max_retries": 0}


# ---- translate_errors ----


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda sdk: sdk.AuthenticationError(), "作り物: 鍵が合わない"),
        (lambda sdk: sdk.RateLimitError(), "作り物: 混んでいる"),
        (lambda sdk: sdk.APIStatusError(402), "作り物: 残高が足りない"),
        (lambda sdk: sdk.APIStatusError(500), "作り物: 届かない (状態 500)"),
        (lambda sdk: sdk.APITimeoutError("作り物"), "作り物: 待ちの上限"),
        (lambda sdk: sdk.APIConnectionError("作り物"), "作り物: 届かない"),
    ],
    ids=["auth", "rate_limit", "status_402", "status_500", "timeout", "connection"],
)
def test_translate_errors_turns_sdk_errors_into_value_error(sdk, make, expected):
    with pytest.raises(ValueError) as caught:
        with ai.translate_errors(MESSAGES):
            raise make(sdk)

    assert type(caught.value) is ValueError
    assert str(caught.value) == expected
    # 元の例外 (返事の本文を持ちうる) は from None で切る。
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "error",
    [KeyError("作り物"), RuntimeError("作り物"), ValueError("作り物")],
    ids=["key_error", "runtime_error", "value_error"],
)
def test_translate_errors_lets_other_errors_through(sdk, error):
    with pytest.raises(type(error)) as caught:
        with ai.translate_errors(MESSAGES):
            raise error

    assert caught.value is error
