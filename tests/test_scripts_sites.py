"""手元の置き場のサーバーをまとめて立てる道具 (scripts/sites.py)。

見る所: 置き場の探し方と port の読み方、立っているかの見方、起動の命令の形、止めるときに名前でなく port の process だけを見ること。
runserver そのものは起こさない (Popen を差し替える)。
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sites.py"


@pytest.fixture(scope="module")
def sites():
    spec = importlib.util.spec_from_file_location("sites_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sites_script"] = module  # dataclass が module を sys.modules から引くため
    spec.loader.exec_module(module)
    return module


def write_site(root: Path, site_id: str, port) -> Path:
    folder = root / site_id
    folder.mkdir(parents=True)
    (folder / "answers.yaml").write_text(f"id: {site_id}\nname: 作り物\nport: {port}\n", encoding="utf-8")
    return folder


def test_find_sites_reads_ports_and_skips_folders_without_answers(sites, tmp_path):
    write_site(tmp_path, "a_co", 8101)
    write_site(tmp_path, "b_co", 8102)
    (tmp_path / "not_a_site").mkdir()

    found = sites.find_sites(tmp_path, [])

    assert [(s.id, s.port) for s in found] == [("a_co", 8101), ("b_co", 8102)]
    assert found[0].log == tmp_path / "a_co.runserver.log"


def test_find_sites_by_id_refuses_missing(sites, tmp_path):
    write_site(tmp_path, "a_co", 8101)
    assert [s.id for s in sites.find_sites(tmp_path, ["a_co"])] == ["a_co"]
    with pytest.raises(ValueError, match="answers.yaml"):
        sites.find_sites(tmp_path, ["zzz"])


def test_bad_port_is_refused(sites, tmp_path):
    write_site(tmp_path, "a_co", "八千")
    with pytest.raises(ValueError, match="port"):
        sites.find_sites(tmp_path, [])


def test_port_is_open_sees_a_listening_socket(sites):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert sites.port_is_open(port) is True
    assert sites.port_is_open(port) is False


def test_start_command_uses_noreload(sites, tmp_path):
    site = sites.Site(id="a_co", path=tmp_path / "a_co", port=8101)
    # Windows では Path("/venv/bin/python3") が "\\venv\\bin\\python3" になるので、字の期待は str(python) で作る
    python = Path("/venv/bin/python3")
    assert sites.start_command(site, python) == [str(python), "manage.py", "runserver", "8101", "--noreload"]


def test_start_skips_when_port_already_open(sites, tmp_path, monkeypatch):
    site = sites.Site(id="a_co", path=write_site(tmp_path, "a_co", 8101), port=8101)
    monkeypatch.setattr(sites, "port_is_open", lambda port, host=sites.HOST, timeout=0.5: True)
    started = []
    monkeypatch.setattr(sites.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert "既に立っている" in sites.start(site, Path("/venv/bin/python3"))
    assert started == []


def test_start_launches_and_waits_for_the_port(sites, tmp_path, monkeypatch):
    site = sites.Site(id="a_co", path=write_site(tmp_path, "a_co", 8101), port=8101)
    answers = iter([False, False, True])
    monkeypatch.setattr(sites, "port_is_open", lambda port, host=sites.HOST, timeout=0.5: next(answers))
    calls = []
    monkeypatch.setattr(sites.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setattr(sites.time, "sleep", lambda s: None)

    line = sites.start(site, Path("/venv/bin/python3"))

    assert "で立った" in line
    assert calls[0][0][1:] == ["manage.py", "runserver", "8101", "--noreload"]
    assert calls[0][1]["cwd"] == site.path and calls[0][1]["start_new_session"] is True
    assert site.log.is_file()


def test_stop_kills_only_listening_pids(sites, tmp_path, monkeypatch):
    site = sites.Site(id="a_co", path=tmp_path / "a_co", port=8101)
    pids = iter([[4242], [4242], []])
    monkeypatch.setattr(sites, "listening_pids", lambda port: next(pids, []))
    killed = []
    monkeypatch.setattr(sites.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(sites.time, "sleep", lambda s: None)

    assert "止めた (pid 4242)" in sites.stop(site)
    assert killed == [(4242, sites.signal.SIGTERM)]


def test_stop_without_process_says_so(sites, tmp_path, monkeypatch):
    site = sites.Site(id="a_co", path=tmp_path / "a_co", port=8101)
    monkeypatch.setattr(sites, "listening_pids", lambda port: [])
    assert "process は無い" in sites.stop(site)


def test_main_status_lists_sites(sites, tmp_path, monkeypatch, capsys):
    write_site(tmp_path, "a_co", 8101)
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))
    monkeypatch.setattr(sites, "port_is_open", lambda port, host=sites.HOST, timeout=0.5: False)
    assert sites.main(["status"]) == 0
    assert "a_co: port 8101 止まっている" in capsys.readouterr().out


def test_main_without_sites_dir_fails(sites, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "none"))
    assert sites.main(["status"]) == 2
    assert "置き場の親が無い" in capsys.readouterr().err
