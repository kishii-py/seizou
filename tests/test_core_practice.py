"""練習モード (seizou.core.practice・命令 seizou_practice・帯と題・雛形の settings の塊)。

process は本当に立てず、practice の 5 つの関数 (port_is_open・verify_settings・launch・wait_ready・terminate) を差し替える。
どの試験も BASE_DIR と MEDIA_ROOT を一時のフォルダに替える (リポジトリに practice/ を作らず、ほかの試験と共有の
MEDIA_ROOT を写さないため)。
"""

import importlib.util
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.urls import reverse

from seizou import cli
from seizou.core import backup, practice

sqlite_only = pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="SQLite の backup API の試験 (PostgreSQL の組では写しを取らない道だけを見る)",
)

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def site(settings, tmp_path):
    site = tmp_path / "site"
    drawings = site / "media" / "drawings"
    drawings.mkdir(parents=True)
    (drawings / "a.png").write_bytes(PNG)
    (site / "manage.py").write_text("# 試験の置き場の印 (本物の manage.py ではない)\n", encoding="utf-8")
    settings.BASE_DIR = site
    settings.MEDIA_ROOT = site / "media"
    settings.SEIZOU = {"id": "sample_co", "port": 8010}
    return site


def run(*args, stdout=None, stderr=None) -> str:
    out = stdout if stdout is not None else io.StringIO()
    err = stderr if stderr is not None else io.StringIO()
    call_command("seizou_practice", *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def users(db: Path) -> set[str]:
    """写しの db.sqlite3 の利用者の名前 (試験の中なので sqlite3 で直に読む)。"""
    con = sqlite3.connect(str(db))
    try:
        return {row[0] for row in con.execute("SELECT username FROM core_user")}
    finally:
        con.close()


def folders(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted(path for path in parent.iterdir() if path.is_dir())


class FakeProc:
    """launch の偽が返す子の process (pid と、poll() が返す終わりの番号だけを持つ)。"""

    def __init__(self, pid, exit_code):
        self.pid = pid
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


def fake_server(monkeypatch, *, open_ports=(), verified=True, ready=True, exit_code=None) -> dict:
    """practice の 5 つの関数を差し替え、呼ばれた引数を辞書に覚える (process は立てない)。

    open = 開いているポートの集まり。pids = pid ごとのポート (terminate がそのポートを閉じる)。
    """
    calls = {"open": set(open_ports), "pids": {}, "launch": [], "verify": [], "wait": [], "terminate": []}

    def port_is_open(port):
        return port in calls["open"]

    def verify_settings(base, env, db_path, media_path):
        calls["verify"].append({"base": base, "env": env, "db_path": db_path, "media_path": media_path})
        return verified

    def launch(args, *, cwd, env, log):
        calls["launch"].append({"args": args, "cwd": cwd, "env": env, "log": log})
        port = int(args[3])
        calls["pids"][4321] = port
        if exit_code is not None:
            log.write_text("Error: That port is already in use.\n", encoding="utf-8")
        if ready:
            calls["open"].add(port)
        return FakeProc(4321, exit_code)

    def wait_ready(proc, port):
        calls["wait"].append(port)
        return ready

    def terminate(pid):
        calls["terminate"].append(pid)
        calls["open"].discard(calls["pids"].get(pid))

    monkeypatch.setattr(practice, "port_is_open", port_is_open)
    monkeypatch.setattr(practice, "verify_settings", verify_settings)
    monkeypatch.setattr(practice, "launch", launch)
    monkeypatch.setattr(practice, "wait_ready", wait_ready)
    monkeypatch.setattr(practice, "terminate", terminate)
    return calls


def write_copy(folder: Path, record: dict | None) -> Path:
    """手で作った練習の写し (偽の db.sqlite3 と、record があれば practice.json)。"""
    folder.mkdir(parents=True)
    (folder / "db.sqlite3").write_text("偽の写し", encoding="utf-8")
    if record is not None:
        (folder / "practice.json").write_text(json.dumps(record), encoding="utf-8")
    return folder


def load_settings(path: Path, name: str):
    """描いた settings.py を読む (sys.modules に入れない)。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same_path(a, b) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def test_scaffold_settings_reads_practice_env(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    text = cli.render("settings.py.tmpl", id="sample_co", seizou_items="    'id': 'sample_co',\n    'port': 8010,")
    (site / "settings.py").write_text(text, encoding="utf-8")
    copy = tmp_path / "copy"
    monkeypatch.setenv("SECRET_KEY", "fake-practice-test-key")
    monkeypatch.delenv("SEIZOU_DB", raising=False)
    monkeypatch.setenv("SEIZOU_PRACTICE_DB", str(copy / "db.sqlite3"))
    monkeypatch.setenv("SEIZOU_PRACTICE_MEDIA", str(copy / "media"))

    practice = load_settings(site / "settings.py", "practice_settings_with_env")
    assert same_path(str(practice.DATABASES["default"]["NAME"]), copy / "db.sqlite3")
    assert same_path(str(practice.MEDIA_ROOT), copy / "media")
    assert practice.SESSION_COOKIE_NAME == "seizou_sample_co_session_practice"
    assert practice.CSRF_COOKIE_NAME == "seizou_sample_co_csrf_practice"

    monkeypatch.delenv("SEIZOU_PRACTICE_DB")
    monkeypatch.delenv("SEIZOU_PRACTICE_MEDIA")
    plain = load_settings(site / "settings.py", "practice_settings_without_env")
    assert same_path(str(plain.DATABASES["default"]["NAME"]), site / "db.sqlite3")
    assert same_path(str(plain.MEDIA_ROOT), site / "media")
    assert plain.SESSION_COOKIE_NAME == "seizou_sample_co_session"
    assert plain.CSRF_COOKIE_NAME == "seizou_sample_co_csrf"
    assert "_practice" not in plain.SESSION_COOKIE_NAME + plain.CSRF_COOKIE_NAME


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_start_copies_db_and_media_and_launches_on_other_port(site, monkeypatch):
    server = fake_server(monkeypatch)
    get_user_model().objects.create_user("before", password="pass-for-tests-only")

    text = run("start")

    found = folders(site / "practice")
    assert len(found) == 1
    folder = found[0]
    assert backup.NAME_PATTERN.match(folder.name)
    assert "before" in users(folder / "db.sqlite3")
    assert (folder / "media" / "drawings" / "a.png").read_bytes() == PNG
    record = json.loads((folder / "practice.json").read_text(encoding="utf-8"))
    assert (record["pid"], record["port"], record["url"]) == (4321, 8110, "http://127.0.0.1:8110/")

    assert len(server["launch"]) == 1
    call = server["launch"][0]
    assert call["args"] == [sys.executable, str(site / "manage.py"), "runserver", "8110", "--noreload", "--insecure"]
    assert call["cwd"] == site
    # 環境変数の辞書は鍵を含むので、比べは真偽にしてから assert する (失敗の文に値を出さない)
    env = call["env"]
    flag_is_one = env.get("SEIZOU_PRACTICE") == "1"
    db_is_copy = same_path(env.get("SEIZOU_PRACTICE_DB", ""), folder / "db.sqlite3")
    media_is_copy = same_path(env.get("SEIZOU_PRACTICE_MEDIA", ""), folder / "media")
    assert flag_is_one
    assert db_is_copy
    assert media_is_copy
    assert len(server["verify"]) == 1
    same_env = server["verify"][0]["env"] is env
    assert same_env

    assert "http://127.0.0.1:8110/" in text
    assert "本番に残りません" in text
    assert "seizou_practice stop" in text
    assert (site / "media" / "drawings" / "a.png").read_bytes() == PNG


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_start_with_port_option(site, monkeypatch):
    server = fake_server(monkeypatch)

    run("start", "--port", "8200")

    assert "8200" in server["launch"][0]["args"]
    [folder] = folders(site / "practice")
    assert json.loads((folder / "practice.json").read_text(encoding="utf-8"))["port"] == 8200


@pytest.mark.django_db
def test_start_refuses_busy_port_postgres_and_missing_things(site, settings, monkeypatch):
    # PostgreSQL の組でも SQLite の置き場として進める (どの道も写しを取る前に止まる)
    monkeypatch.setattr(backup, "database_kind", lambda: "sqlite")
    server = fake_server(monkeypatch, open_ports=(8110,))

    with pytest.raises(CommandError) as busy:
        run("start")
    assert "既に使われています" in str(busy.value)

    monkeypatch.setattr(backup, "database_kind", lambda: "postgresql")
    with pytest.raises(CommandError) as postgres:
        run("start")
    assert "SQLite の置き場だけ" in str(postgres.value)

    monkeypatch.setattr(backup, "database_kind", lambda: "sqlite")
    settings.SEIZOU = {}
    with pytest.raises(CommandError) as no_port:
        run("start")
    assert "--port" in str(no_port.value)

    (site / "manage.py").unlink()
    with pytest.raises(CommandError) as no_manage:
        run("start")
    assert "manage.py が見つかりません" in str(no_manage.value)

    assert folders(site / "practice") == []
    assert server["launch"] == []
    assert server["verify"] == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_start_stops_when_settings_do_not_read_copy(site, monkeypatch):
    server = fake_server(monkeypatch, verified=False)

    with pytest.raises(CommandError) as info:
        run("start")

    assert "docs/operations.md" in str(info.value)
    assert folders(site / "practice") == []
    assert len(server["verify"]) == 1
    assert server["launch"] == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_start_cleans_up_when_server_exits(site, monkeypatch):
    fake_server(monkeypatch, ready=False, exit_code=1)

    with pytest.raises(CommandError) as info:
        run("start")

    assert "立ちませんでした" in str(info.value)
    assert "already in use" in str(info.value)
    assert folders(site / "practice") == []


@pytest.mark.django_db
def test_stop_terminates_only_recorded_process_and_removes_only_copies(site, monkeypatch):
    root = site / "practice"
    running = write_copy(root / "20261103-210000", {"pid": 4321, "port": 8110, "url": "http://127.0.0.1:8110/"})
    stopped = write_copy(root / "20261103-210500", {"pid": 4322, "port": 8111, "url": "http://127.0.0.1:8111/"})
    keep = write_copy(root / "keep-me", {"pid": 4323, "port": 8112, "url": "http://127.0.0.1:8112/"})
    no_record = write_copy(root / "20261103-211000", None)
    (site / "db.sqlite3").write_text("本番の偽の DB", encoding="utf-8")
    (site / "backups").mkdir()
    (site / "backups" / "x").write_text("控えの偽", encoding="utf-8")
    server = fake_server(monkeypatch, open_ports=(8110, 8112))
    server["pids"].update({4321: 8110, 4322: 8111, 4323: 8112})

    text = run("stop")

    assert server["terminate"] == [4321]
    assert not running.exists()
    assert not stopped.exists()
    assert keep.is_dir()
    assert no_record.is_dir()
    assert root.is_dir()
    assert (site / "db.sqlite3").read_text(encoding="utf-8") == "本番の偽の DB"
    assert (site / "backups" / "x").is_file()
    assert (site / "media" / "drawings" / "a.png").read_bytes() == PNG
    assert "止めて写しを消しました" in text
    assert "既に止まっていました" in text
    assert "practice.json が無い" in text


@pytest.mark.django_db
def test_stop_keeps_copy_when_process_does_not_stop(site, monkeypatch):
    monkeypatch.setattr(practice, "STOP_WAIT_SECONDS", 0)
    folder = write_copy(site / "practice" / "20261103-210000", {"pid": 4321, "port": 8110})
    server = fake_server(monkeypatch, open_ports=(8110,))

    def stubborn(pid):
        server["terminate"].append(pid)

    monkeypatch.setattr(practice, "terminate", stubborn)
    err = io.StringIO()

    with pytest.raises(CommandError) as info:
        run("stop", stderr=err)

    assert "止まらない" in str(info.value)
    assert "pid 4321" in err.getvalue()
    assert server["terminate"] == [4321]
    assert folder.is_dir()


@pytest.mark.django_db
def test_status_and_stop_without_copies(site, monkeypatch):
    server = fake_server(monkeypatch)
    assert "練習のサーバーはありません" in run("status")
    assert "練習のサーバーはありません" in run("stop")

    write_copy(
        site / "practice" / "20261103-210000",
        {"pid": 4321, "port": 8110, "url": "http://127.0.0.1:8110/", "started_at": "2026-11-03T21:00:00"},
    )
    server["open"].add(8110)
    text = run("status")
    assert "立っている" in text
    assert "http://127.0.0.1:8110/" in text

    server["open"].discard(8110)
    assert "止まっている" in run("status")


def test_is_practice_reads_env_only(monkeypatch):
    monkeypatch.delenv("SEIZOU_PRACTICE", raising=False)
    assert practice.is_practice() is False
    monkeypatch.setenv("SEIZOU_PRACTICE", "1")
    assert practice.is_practice() is True
    for value in ("0", "yes", ""):
        monkeypatch.setenv("SEIZOU_PRACTICE", value)
        assert practice.is_practice() is False


BAND = "練習モード (ここでの登録は本番に残りません。取引先へは送りません。閉じるときは seizou_practice stop)"
BAND_CLASS = 'class="bg-warning text-warning-content px-4 py-2 text-center text-sm font-bold"'


@pytest.mark.django_db
def test_band_and_title_only_in_practice_mode(client, monkeypatch):
    monkeypatch.delenv("SEIZOU_PRACTICE", raising=False)
    response = client.get(reverse("core:login"))
    html = response.content.decode()
    assert response.status_code == 200
    assert response.context["seizou"]["practice"] is False
    assert "練習モード" not in html
    assert "<title>練習: " not in html

    monkeypatch.setenv("SEIZOU_PRACTICE", "1")
    response = client.get(reverse("core:login"))
    html = response.content.decode()
    assert response.status_code == 200
    assert response.context["seizou"]["practice"] is True
    assert BAND in html
    assert "<title>練習: " in html
    assert BAND_CLASS in html

    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    client.force_login(user)
    response = client.get(reverse("core:home"))
    html = response.content.decode()
    assert response.status_code == 200
    assert BAND in html
    assert "<title>練習: " in html
    assert html.index("<body") < html.index(BAND) < html.index('<div class="drawer')


def test_practice_env_blanks_partner_secrets(tmp_path, monkeypatch):
    """練習の子の process には取引先とのつながりの鍵を渡さない (空の字。settings.py の setdefault が .env から入れ直さない)。"""
    monkeypatch.setenv("SEIZOU_PARTNER_B_PLATING_SECRET", "whsec_dummy_value_for_test")
    monkeypatch.setenv("SEIZOU_PARTNER_A_MACHINING_SECRET", "whsec_dummy_value_for_test_2")
    monkeypatch.setenv("SEIZOU_OTHER_VALUE", "keep")
    env = practice.practice_env(tmp_path)
    assert env["SEIZOU_PARTNER_B_PLATING_SECRET"] == ""
    assert env["SEIZOU_PARTNER_A_MACHINING_SECRET"] == ""
    assert env["SEIZOU_OTHER_VALUE"] == "keep"
    assert env[practice.ENV_FLAG] == "1"
    assert practice.is_partner_secret("SEIZOU_PARTNER_X_SECRET") is True
    assert practice.is_partner_secret("SEIZOU_PARTNER_X") is False
    assert practice.is_partner_secret("SECRET_KEY") is False
