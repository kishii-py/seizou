"""練習モードの子の process を、Windows では端末の窓から離して立てる (seizou.core.practice.launch) 試験 (依頼書 N394)。

本物の process は立てない。practice.subprocess.Popen を、受けた引数を覚える偽に差し替えて見る。
OS の見分けは practice._on_windows を差し替える。os.name そのものを nt にすると、その間に作る Path が
WindowsPath になるので、os.name を差し替える 1 本 (c) は、差し替えの間に _on_windows を呼ぶだけにする。
Windows の実機で窓を閉じても練習のサーバーが残るかは、この試験では確かめない (引数だけを見る)。
"""

import subprocess

from seizou.core import practice

#: Windows の subprocess の CREATE_NEW_PROCESS_GROUP と DETACHED_PROCESS の値
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


class FakeProcess:
    pid = 4321


def fake_popen(monkeypatch) -> list[dict]:
    """practice.subprocess.Popen を偽に差し替え、受けた引数 (args と keyword) の並びを返す。"""
    calls = []

    def popen(args, **kwargs):
        calls.append({"args": list(args), **kwargs})
        return FakeProcess()

    monkeypatch.setattr(practice.subprocess, "Popen", popen)
    return calls


def test_launch_on_windows_detaches_from_console(tmp_path, monkeypatch):
    monkeypatch.setattr(practice, "_on_windows", lambda: True)
    calls = fake_popen(monkeypatch)

    proc = practice.launch(["python", "manage.py", "runserver"], cwd=tmp_path, env={}, log=tmp_path / "runserver.log")

    assert proc.pid == 4321
    assert len(calls) == 1
    assert calls[0]["creationflags"] == CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    assert "start_new_session" not in calls[0]
    assert calls[0]["stderr"] == subprocess.STDOUT
    assert (tmp_path / "runserver.log").is_file()


def test_launch_on_posix_starts_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(practice, "_on_windows", lambda: False)
    calls = fake_popen(monkeypatch)

    practice.launch(["python", "manage.py", "runserver"], cwd=tmp_path, env={}, log=tmp_path / "runserver.log")

    assert len(calls) == 1
    assert calls[0]["start_new_session"] is True
    assert "creationflags" not in calls[0]


def test_on_windows_reads_os_name(monkeypatch):
    # os.name を差し替えている間は Path を作らない (assert の失敗の報告も Path を作るので、外で比べる)
    with monkeypatch.context() as patch:
        patch.setattr(practice.os, "name", "nt")
        on_nt = practice._on_windows()
    with monkeypatch.context() as patch:
        patch.setattr(practice.os, "name", "posix")
        on_posix = practice._on_windows()

    assert on_nt is True
    assert on_posix is False
