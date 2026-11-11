"""戻しの命令 (seizou_restore) が、置き場のポートで待ち受けがあるときに 1 行で知らせてから聞く事を見る (依頼書 N422)。

置き場のサーバーが立っていそうなとき (会社のポートに 127.0.0.1 でつながる) は、何が起きるかの 1 行を出してから、
今と同じ段落と問いを出す。--yes のときは 1 行を出して問わずに戻す。止めはしない。ポートが無い・見られないときは
今のとおり知らせずに聞く。本物のポートは見ない (practice.port_is_open を必ず差し替える)。
出所 = 精査 Q4 (2026-11-10) の D2 と、本体の決め 11-11 14:2x。
"""

import pytest

from seizou.core import practice
from tests.test_core_backup import (  # noqa: F401 - fixture を借りる
    make_user,
    plain_backups,
    retreats,
    run,
    site,
    sqlite_only,
    user_exists,
)

#: 知らせの 1 行の中の字 (問いの段落の「止めてから進めてください」と分ける)
NOTICE = "サーバーが立っているようです"
NOTICE_PORT = "ポート 8010"


def _backup_then_change(base) -> str:
    """控えを 1 つ取り、その後に利用者 after を足す。控えの名前を返す。"""
    run("seizou_backup")
    name = plain_backups(base / "backups")[0].name
    make_user("after")
    return name


def _port_open(monkeypatch, answer):
    """practice.port_is_open を差し替える。answer が例外の class なら、それを出す。呼ばれたポートの並びを返す。"""
    calls = []

    def fake(port):
        calls.append(port)
        if isinstance(answer, type) and issubclass(answer, BaseException):
            raise answer("作り物の誤り")
        return answer

    monkeypatch.setattr(practice, "port_is_open", fake)
    return calls


def _answer(monkeypatch, text):
    """input を差し替える。呼ばれた問いの並びを返す。"""
    prompts = []

    def fake(prompt=""):
        prompts.append(prompt)
        return text

    monkeypatch.setattr("builtins.input", fake)
    return prompts


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_running_server_notice_then_no_keeps_everything(site, monkeypatch):  # noqa: F811
    """(a) 待ち受けがあり、問いに n と答えると、1 行を出してから聞き、やめて何も変えない。"""
    name = _backup_then_change(site)
    calls = _port_open(monkeypatch, True)
    prompts = _answer(monkeypatch, "n")

    out = run("seizou_restore", name)

    assert NOTICE in out
    assert NOTICE_PORT in out
    assert out.index(NOTICE) < out.index("止めてから進めてください")
    assert out.rstrip().endswith("やめました。何も変えていません。")
    assert calls == [8010]
    assert len(prompts) == 1
    assert user_exists("after")
    assert retreats(site / "backups") == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_running_server_notice_then_yes_restores(site, monkeypatch):  # noqa: F811
    """(b) 待ち受けがあり、問いに yes と答えると、1 行の後に戻す。"""
    name = _backup_then_change(site)
    _port_open(monkeypatch, True)
    prompts = _answer(monkeypatch, "yes")

    out = run("seizou_restore", name)

    assert NOTICE in out
    assert NOTICE_PORT in out
    assert "戻しました" in out
    assert out.index(NOTICE) < out.index("戻しました")
    assert len(prompts) == 1
    assert not user_exists("after")
    assert len(retreats(site / "backups")) == 1


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_running_server_notice_with_yes_flag_does_not_ask(site, monkeypatch):  # noqa: F811
    """(c) 待ち受けがあり --yes を付けると、問わずに 1 行を出して戻す (止めない)。"""
    name = _backup_then_change(site)
    _port_open(monkeypatch, True)

    def must_not_ask(prompt=""):
        raise AssertionError("--yes のときに問いを出した")

    monkeypatch.setattr("builtins.input", must_not_ask)

    out = run("seizou_restore", name, "--yes")

    assert NOTICE in out
    assert NOTICE_PORT in out
    assert out.index(NOTICE) < out.index("戻しました")
    assert not user_exists("after")
    assert len(retreats(site / "backups")) == 1


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_no_server_no_notice(site, monkeypatch):  # noqa: F811
    """(d) 待ち受けが無いと、1 行を出さない (今の出力のまま)。"""
    name = _backup_then_change(site)
    calls = _port_open(monkeypatch, False)
    prompts = _answer(monkeypatch, "n")

    out = run("seizou_restore", name)

    assert NOTICE not in out
    assert "止めてから進めてください" in out
    assert "やめました" in out
    assert calls == [8010]
    assert len(prompts) == 1
    assert user_exists("after")


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_port_check_error_still_asks(site, monkeypatch):  # noqa: F811
    """(e) ポートを見る所が OSError を出しても、命令は止まらず、1 行を出さずに今のとおり聞く。"""
    name = _backup_then_change(site)
    calls = _port_open(monkeypatch, OSError)
    prompts = _answer(monkeypatch, "n")

    out = run("seizou_restore", name)

    assert calls == [8010]
    assert NOTICE not in out
    assert len(prompts) == 1
    assert "やめました" in out
    assert user_exists("after")
    assert retreats(site / "backups") == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_no_company_port_does_not_look(site, settings, monkeypatch):  # noqa: F811
    """(f) settings の SEIZOU に port が無いときは、ポートを見ない (呼ばれたら並びに残るので、空の事を見る)。"""
    name = _backup_then_change(site)
    settings.SEIZOU = {"id": "sample_co"}
    calls = _port_open(monkeypatch, True)
    prompts = _answer(monkeypatch, "n")

    out = run("seizou_restore", name)

    assert calls == []
    assert NOTICE not in out
    assert len(prompts) == 1
    assert "やめました" in out
