"""社内の PC でサーバーを起こす命令 (manage.py seizou_serve) と、LAN の中だけで http で開く設定 (SEIZOU_LAN_HTTP)。

本人の決め 11-14 (社内の PC 1 台を LAN の中だけで使う形を本番の形にし、サーバーを起こす命令を足す)。
実際に待ち受ける所 (waitress) は呼ばず、渡す物と、CSS と JS を返す WSGI の包みを見る。
"""

import ast
import io
import string
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

from seizou.core import doctor, middleware, serve


def wsgi_get(app, path):
    """WSGI の app に GET を 1 回送り、(状態の字, 見出しの辞書, 本文) を返す。"""
    from wsgiref.util import setup_testing_defaults

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "", "wsgi.input": io.BytesIO(b"")}
    setup_testing_defaults(environ)
    environ["PATH_INFO"] = path
    environ["HTTP_HOST"] = "testserver"
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"] = status
        seen["headers"] = {name.lower(): value for name, value in headers}

    chunks = app(environ, start_response)
    try:
        body = b"".join(chunks)
    finally:
        if hasattr(chunks, "close"):
            chunks.close()
    return seen["status"], seen["headers"], body


def test_app_sends_static_files_without_debug_and_without_collectstatic(settings, tmp_path):
    """本番の設定 (DEBUG=0) でも、集めた物 (STATIC_ROOT) が無くても、部品の CSS と JS を返す。"""
    settings.DEBUG = False
    settings.STATIC_ROOT = tmp_path / "empty"
    app = serve.build_app()

    status, headers, body = wsgi_get(app, "/static/css/app.css")
    assert status.startswith("200")
    assert headers["content-type"].startswith("text/css")
    assert len(body) > 1000

    status, _, body = wsgi_get(app, "/static/js/seizou-modal.js")
    assert status.startswith("200")
    assert body


def test_site_static_folder_wins_over_the_package(settings, tmp_path):
    """置き場の static/ に同じ名前のファイルがあれば、そちらを返す (Django の探し方と同じ順)。"""
    folder = tmp_path / "static"
    (folder / "css").mkdir(parents=True)
    (folder / "css" / "app.css").write_text("/* 置き場の CSS */", encoding="utf-8")
    settings.STATICFILES_DIRS = [folder]

    _, _, body = wsgi_get(serve.build_app(), "/static/css/app.css")
    assert body == "/* 置き場の CSS */".encode()


@pytest.mark.django_db
def test_other_paths_go_to_django(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    status, _, _ = wsgi_get(serve.build_app(), "/health/")
    assert status.startswith("200")
    status, _, _ = wsgi_get(serve.build_app(), "/static/css/no-such-file.css")
    assert status.startswith("404")


def test_command_passes_host_port_and_threads(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(serve, "run", lambda host, port, threads: calls.append((host, port, threads)))
    settings.SEIZOU = {"port": 8123}

    out = io.StringIO()
    call_command("seizou_serve", stdout=out)
    assert calls == [("0.0.0.0", 8123, serve.DEFAULT_THREADS)]
    assert "8123" in out.getvalue()

    call_command("seizou_serve", "--host", "127.0.0.1", "--port", "18999", "--threads", "2", stdout=io.StringIO())
    assert calls[-1] == ("127.0.0.1", 18999, 2)


def test_command_needs_a_port_when_settings_have_none(settings, monkeypatch):
    from django.core.management.base import CommandError

    monkeypatch.setattr(serve, "run", lambda host, port, threads: None)
    settings.SEIZOU = {}
    with pytest.raises(CommandError) as caught:
        call_command("seizou_serve", stdout=io.StringIO())
    assert "--port" in str(caught.value)


def test_command_tells_about_http_login(settings, monkeypatch):
    """Cookie が HTTPS だけの設定のまま起こしたら、http ではログインできない事と直し方を 1 行で知らせる (止めはしない)。"""
    monkeypatch.setattr(serve, "run", lambda host, port, threads: None)
    settings.SEIZOU = {"port": 8123}
    settings.SESSION_COOKIE_SECURE = True
    out = io.StringIO()
    call_command("seizou_serve", stdout=out)
    assert "SEIZOU_LAN_HTTP=1" in out.getvalue()

    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    out = io.StringIO()
    call_command("seizou_serve", stdout=out)
    assert "SEIZOU_LAN_HTTP=1" not in out.getvalue()


def test_dev_auto_login_stays_off_under_the_command(settings, monkeypatch):
    """開発の設定 (DEBUG=1・自動ログイン 1) のまま起こしても、ログインを飛ばさない (社内のほかの端末から開けるため)。"""
    settings.DEBUG = True
    settings.SEIZOU_DEV_AUTO_LOGIN = True
    monkeypatch.setattr(sys, "argv", ["manage.py", "seizou_serve"])
    assert middleware.dev_auto_login_enabled() is False


def _template_tree():
    import seizou.scaffold

    text = (Path(seizou.scaffold.__file__).parent / "settings.py.tmpl").read_text(encoding="utf-8")
    return ast.parse(string.Template(text).safe_substitute(id="t", seizou_items=""))


def test_scaffold_lan_http_switch_turns_the_https_only_lines_off():
    """ひな形: .env の SEIZOU_LAN_HTTP=1 で、Cookie を http でも送り、proxy の見出しを信じない。本番の塊より後ろに書く。"""
    body = _template_tree().body
    names = [
        node.targets[0].id
        for node in body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
    ]
    assert "SEIZOU_LAN_HTTP" in names

    def is_if(node, name, negated):
        if not isinstance(node, ast.If):
            return False
        test = node.test
        if negated:
            return isinstance(test, ast.UnaryOp) and isinstance(test.operand, ast.Name) and test.operand.id == name
        return isinstance(test, ast.Name) and test.id == name

    production = [index for index, node in enumerate(body) if is_if(node, "DEBUG", True)]
    lan = [index for index, node in enumerate(body) if is_if(node, "SEIZOU_LAN_HTTP", False)]
    assert len(production) == 1 and len(lan) == 1
    assert lan[0] > production[0]
    values = {node.targets[0].id: ast.literal_eval(node.value) for node in body[lan[0]].body if isinstance(node, ast.Assign)}
    assert values == {"SESSION_COOKIE_SECURE": False, "CSRF_COOKIE_SECURE": False, "SECURE_PROXY_SSL_HEADER": None}


def test_scaffold_env_names_the_switch():
    import seizou.scaffold

    text = (Path(seizou.scaffold.__file__).parent / "env.tmpl").read_text(encoding="utf-8")
    assert "SEIZOU_LAN_HTTP=1" in text
    assert not [line for line in text.splitlines() if line.startswith("SEIZOU_LAN_HTTP")]


def test_doctor_cookie_row_is_good_with_the_lan_switch(settings):
    settings.DEBUG = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.SEIZOU_LAN_HTTP = False
    assert doctor.check_cookies()[0].ok is False

    settings.SEIZOU_LAN_HTTP = True
    row = doctor.check_cookies()[0]
    assert row.ok is True
    assert "SEIZOU_LAN_HTTP" in row.detail


def test_doctor_collectstatic_row_is_good_with_the_lan_switch(settings, tmp_path):
    settings.STATIC_ROOT = tmp_path / "empty"
    settings.SEIZOU_LAN_HTTP = False
    row = doctor.check_collectstatic()[0]
    assert row.ok is False
    assert "seizou_serve" in row.fix

    settings.SEIZOU_LAN_HTTP = True
    row = doctor.check_collectstatic()[0]
    assert row.ok is True
    assert "seizou_serve" in row.detail


def test_command_flushes_the_lines_before_it_starts_to_wait(settings, monkeypatch):
    """案内の行は、待ち受けを始める前に書き出す (記録のファイルへ流すと、止めるまで行が出なかった。11-14 に実際に起こして見た)。"""

    class Stream(io.StringIO):
        flushed_with = None

        def flush(self):
            type(self).flushed_with = self.getvalue()
            super().flush()

    seen = {}
    out = Stream()
    monkeypatch.setattr(serve, "run", lambda host, port, threads: seen.setdefault("at_run", Stream.flushed_with))
    settings.SEIZOU = {"port": 8123}
    call_command("seizou_serve", stdout=out)
    assert seen["at_run"] is not None
    assert "サーバーを起こします" in seen["at_run"]


def test_run_does_not_touch_the_logging_setup(monkeypatch):
    """waitress の serve() は logging.basicConfig() を呼び、置き場の記録の行が 2 回ずつ出る。create_server で起こす。"""
    import logging

    import waitress

    calls = []

    class Server:
        def run(self):
            calls.append("run")

    def create_server(app, **kwargs):
        calls.append(kwargs)
        return Server()

    monkeypatch.setattr(waitress, "create_server", create_server)
    monkeypatch.setattr(waitress, "serve", lambda *args, **kwargs: pytest.fail("serve() を呼ばない"))
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: pytest.fail("basicConfig を呼ばない"))
    monkeypatch.setattr(serve, "build_app", lambda: object())

    serve.run("127.0.0.1", 18999, 2)
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 18999 and calls[0]["threads"] == 2
    assert calls[-1] == "run"


def test_command_tells_when_settings_do_not_read_the_switch(settings, monkeypatch):
    """前に作った置き場: .env に印があっても settings.py に印を読む行が無いと働かない。その時は、settings.py に足す案内を出す。"""
    monkeypatch.setattr(serve, "run", lambda host, port, threads: None)
    monkeypatch.setenv("SEIZOU_LAN_HTTP", "1")
    settings.SEIZOU = {"port": 8123}
    settings.DEBUG = False
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SEIZOU_LAN_HTTP = False
    assert serve.lan_http_unread() is True

    out = io.StringIO()
    call_command("seizou_serve", stdout=out)
    text = out.getvalue()
    assert "settings.py" in text
    assert "印を読む行" in text
    assert ".env に SEIZOU_LAN_HTTP=1 の行を足して" not in text

    settings.SEIZOU_LAN_HTTP = True
    assert serve.lan_http_unread() is False
    monkeypatch.delenv("SEIZOU_LAN_HTTP")
    settings.SEIZOU_LAN_HTTP = False
    assert serve.lan_http_unread() is False


def test_doctor_cookie_row_says_fix_when_settings_do_not_read_the_switch(settings, monkeypatch):
    """点検: .env に印があるのに settings.py が読んでいない置き場は、Cookie の行を直すにして、settings.py に足す行を案内する。"""
    monkeypatch.setenv("SEIZOU_LAN_HTTP", "1")
    settings.DEBUG = False
    settings.SEIZOU_LAN_HTTP = False
    for secure in (True, False):
        settings.SESSION_COOKIE_SECURE = secure
        settings.CSRF_COOKIE_SECURE = secure
        row = doctor.check_cookies()[0]
        assert row.ok is False
        assert "印を読む行" in row.detail
        assert "settings.py" in row.fix and "社内の PC で動かす" in row.fix

    monkeypatch.delenv("SEIZOU_LAN_HTTP")
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    assert doctor.check_cookies()[0].ok is True


def test_restore_question_names_the_new_command():
    """戻す前の確かめの文は、止めるサーバーの例に seizou_serve も挙げる。"""
    import seizou.core.management.commands.seizou_restore as command

    text = Path(command.__file__).read_text(encoding="utf-8")
    assert "runserver / gunicorn / seizou_serve" in text


def test_run_says_what_to_do_when_the_port_is_taken(monkeypatch):
    """待ち受けを始められない時 (同じポートでもう立っている) は、OS の誤りの字のままにせず、次にすることを 1 行で出す。"""
    import waitress

    def create_server(app, **kwargs):
        raise OSError(48, "Address already in use")

    monkeypatch.setattr(waitress, "create_server", create_server)
    monkeypatch.setattr(serve, "build_app", lambda: object())
    with pytest.raises(serve.ServeError) as caught:
        serve.run("0.0.0.0", 18999, 2)
    text = str(caught.value)
    assert text.startswith("サーバーを起こせません。")
    assert "18999" in text and "--port" in text


def test_command_turns_the_start_error_into_one_line(settings, monkeypatch):
    from django.core.management.base import CommandError

    def run(host, port, threads):
        raise serve.ServeError("サーバーを起こせません。試験の文。")

    monkeypatch.setattr(serve, "run", run)
    settings.SEIZOU = {"port": 8123}
    with pytest.raises(CommandError) as caught:
        call_command("seizou_serve", stdout=io.StringIO())
    assert "サーバーを起こせません。" in str(caught.value)


def test_default_threads_is_the_same_as_the_vps_guide():
    """同時にさばく数の既定は、VPS の手順 (gunicorn --threads 4) と同じ 4。それより多い数を確かめた事が無いため。"""
    assert serve.DEFAULT_THREADS == 4
    text = (Path(__file__).resolve().parent.parent / "docs" / "vps.md").read_text(encoding="utf-8")
    assert "--threads 4" in text


def test_operations_guide_warns_about_the_client_ip_header():
    """nginx の無い置き場で接続元の見出しを信じると、端末が接続元を偽れる。手引きの「社内の PC で動かす」に書かない注意がある。"""
    text = (Path(__file__).resolve().parent.parent / "docs" / "operations.md").read_text(encoding="utf-8")
    section = text.split("## 社内の PC で動かす", 1)[1].split("\n## ", 1)[0]
    assert "SEIZOU_CLIENT_IP_HEADER" in section
    assert "書きません" in section
