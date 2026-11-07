"""点検 (seizou.core.doctor・命令 seizou_doctor)。

置き場を一時のフォルダに作り、良い / 直す の行と終わりの番号を見る。鍵の値が出ない事を見る。
値を含む字の比べは、失敗の文に値が出ないよう真偽にしてから assert する。
"""

import io
import os
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.checks import Error
from django.core.management import call_command
from django.core.management.base import CommandError

from seizou.connect.services import register_connection
from seizou.core import doctor
from seizou.core.logging_conf import default_logging
from seizou.masters.services import create_partner

FAKE_SECRET_KEY = "fake-secret-key-must-not-appear-0123456789-abcdefghijklmnopqrstuvwxyz"
FAKE_ENV_SECRET_KEY = "fake-env-secret-key-must-not-appear"
FAKE_PARTNER_SECRET = "fake-partner-secret-must-not-appear"
FAKE_DB_PASSWORD = "fake-db-password-must-not-appear"
FAKE_AI_KEY = "fake-ai-key-must-not-appear"
PARTNER_ENV = "SEIZOU_PARTNER_TESTCO_SECRET"


@pytest.fixture
def site(settings, tmp_path):
    site = tmp_path / "site"
    (site / "media").mkdir(parents=True)
    css = site / "staticfiles" / "css"
    css.mkdir(parents=True)
    (css / "app.css").write_text("/* 試験の偽の CSS */\n", encoding="utf-8")
    settings.BASE_DIR = site
    settings.MEDIA_ROOT = site / "media"
    settings.STATIC_ROOT = site / "staticfiles"
    settings.SECRET_KEY = "fake-" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_aBcDeFgHiJkLmNoPqRsTu"
    settings.SEIZOU_DEV_AUTO_LOGIN = False
    settings.SEIZOU_FILE_DELIVERY = "django"
    settings.ALLOWED_HOSTS = ["testserver", "example.test"]
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SEIZOU = {}
    settings.SEIZOU["access_control"] = {"department": "on", "write": "on", "money": "on"}
    # LOGGING を変えても Django に記録の形を当て直させない (当て直すと seizou の logger の形が後の試験に残る。点検は LOGGING が空でないかだけを見る)
    settings.LOGGING_CONFIG = None
    settings.LOGGING = default_logging()
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "seizou.core.middleware.HtmxLoginMiddleware"]
    env = site / ".env"
    if os.name == "nt":
        env.write_text("SECRET_KEY=fake-env-value-that-must-not-appear\n", encoding="utf-8")
    else:
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("SECRET_KEY=fake-env-value-that-must-not-appear\n")
    return site


def run_doctor(*args) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command("seizou_doctor", *args, stdout=out, stderr=err)
        code = 0
    except CommandError as exc:
        err.write(str(exc))
        code = exc.returncode
    return code, out.getvalue() + err.getvalue()


def fix_lines(out) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("直す ")]


def good_lines(out) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("良い ")]


@pytest.mark.django_db
def test_all_good_exits_zero(site):
    code, out = run_doctor()

    assert code == 0, fix_lines(out)
    assert fix_lines(out) == []
    assert len(good_lines(out)) >= 8
    assert "点検の結果: 良い" in out
    assert "--production" not in out
    assert "check --deploy" in out


@pytest.mark.django_db
def test_fixes_are_listed_with_how_and_exit_one(site, settings):
    settings.SEIZOU_DEV_AUTO_LOGIN = True
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.SECRET_KEY = "short"
    settings.SEIZOU = {"over_shipment": "bogus"}
    settings.SEIZOU_FILE_DELIVERY = "nginx"
    deploy = site / "deploy"
    deploy.mkdir()
    (deploy / "nginx-files.conf").write_text(
        "# internal; を消した断片 (試験の作り物)\nlocation /seizou-files/ {\n    alias \"/somewhere/else/\";\n}\n",
        encoding="utf-8",
    )
    (site / "staticfiles" / "css" / "app.css").unlink()

    code, out = run_doctor()

    assert code == 1
    fixes = fix_lines(out)
    assert len(fixes) >= 7, fixes
    assert all("→ 直し方:" in line for line in fixes)
    text = "\n".join(fixes)
    for word in ("SEIZOU_DEV_AUTO_LOGIN", "ALLOWED_HOSTS", "SECRET_KEY", "over_shipment", "alias", "internal", "collectstatic"):
        assert word in text, word
    summary = [line for line in out.splitlines() if line.startswith("点検の結果:")]
    assert len(summary) == 1
    count = int(summary[0].split("直す ")[1].split(" 件")[0])
    assert count >= 7
    leaked = "short" in out
    assert not leaked, "出力に SECRET_KEY の値が出た"


@pytest.mark.django_db
def test_pending_migrations_and_check_errors(site, monkeypatch):
    monkeypatch.setattr(doctor.MigrationExecutor, "migration_plan", lambda self, targets, clean_start=False: [("x", False)])
    monkeypatch.setattr(doctor, "run_checks", lambda: [Error("試験の偽の誤り", id="seizou.E999")])

    code, out = run_doctor()

    assert code == 1
    text = "\n".join(fix_lines(out))
    assert "manage.py migrate" in text
    assert "manage.py check" in text
    assert "点検そのものが失敗" not in out


def make_send_connection():
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    partner = create_partner({"code": "SC01", "name": "作り物の取引先", "role": "both"}, user)
    return register_connection(partner, "http://127.0.0.1:8002/connect/receive/order/", PARTNER_ENV, "send", user)


@pytest.mark.django_db
def test_connection_secret_and_https(site, settings, monkeypatch):
    make_send_connection()
    monkeypatch.delenv(PARTNER_ENV, raising=False)

    code, out = run_doctor()

    assert code == 1
    fixes = fix_lines(out)
    assert any("seizou_secret" in line and PARTNER_ENV in line for line in fixes)
    assert any("https://" in line for line in fixes)

    monkeypatch.setenv(PARTNER_ENV, FAKE_PARTNER_SECRET)
    code, out = run_doctor()
    assert any(PARTNER_ENV in line for line in good_lines(out))
    assert not any(PARTNER_ENV in line for line in fix_lines(out))
    leaked = FAKE_PARTNER_SECRET in out
    assert not leaked, "出力に取引先の鍵の値が出た"

    settings.DEBUG = True
    code, out = run_doctor()
    assert not any("受け口の URL" in line for line in out.splitlines())
    code, out = run_doctor("--production")
    assert any("受け口の URL" in line for line in fix_lines(out))


@pytest.mark.django_db
def test_dev_mode_skips_production_rows(site, settings):
    settings.DEBUG = True
    settings.SEIZOU_DEV_AUTO_LOGIN = True

    code, out = run_doctor()

    assert code == 0, fix_lines(out)
    assert "--production で見る" in out
    assert not any("自動ログイン" in line for line in fix_lines(out))

    code, out = run_doctor("--production")

    assert code == 1
    assert any("自動ログイン" in line for line in fix_lines(out))


@pytest.mark.skipif(os.name == "nt", reason="Windows では chmod で権限を変えられない")
@pytest.mark.django_db
def test_env_file_permission(site):
    env = site / ".env"
    env.chmod(0o644)

    code, out = run_doctor()

    assert code == 1
    assert any("chmod 600" in line for line in fix_lines(out))

    env.unlink()
    code, out = run_doctor()

    assert code == 0, fix_lines(out)
    assert any(".env" in line and "環境変数" in line for line in good_lines(out))


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="Windows と root では、書けないフォルダを chmod で作れない")
@pytest.mark.django_db
def test_media_root_not_writable(site, settings):
    media = Path(settings.MEDIA_ROOT)
    media.chmod(0o500)
    try:
        code, out = run_doctor()
    finally:
        media.chmod(0o700)

    assert code == 1
    assert any("MEDIA_ROOT" in line for line in fix_lines(out))

    media.rmdir()
    code, out = run_doctor()

    assert code == 0, fix_lines(out)
    assert any("MEDIA_ROOT" in line and "まだ無い" in line for line in good_lines(out))


@pytest.mark.django_db
def test_css_missing_and_bad_delivery_value(site, settings, monkeypatch):
    monkeypatch.setattr(doctor.finders, "find", lambda path, *args, **kwargs: None)

    code, out = run_doctor()

    assert code == 1
    assert any("CSS" in line and "入れ直す" in line for line in fix_lines(out))

    settings.SEIZOU_FILE_DELIVERY = "cdn"
    code, out = run_doctor()

    assert any("django か nginx" in line for line in fix_lines(out))


@pytest.mark.django_db
def test_doctor_never_prints_secret_values_and_reads_no_environ(site, settings, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", FAKE_ENV_SECRET_KEY)
    monkeypatch.setenv(PARTNER_ENV, FAKE_PARTNER_SECRET)
    monkeypatch.setenv("PGPASSWORD", FAKE_DB_PASSWORD)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_AI_KEY)
    settings.SECRET_KEY = FAKE_SECRET_KEY
    make_send_connection()

    # 良いの道 (鍵がそろっている) と、直すの道 (SECRET_KEY が短い・取引先の鍵が無い・本番の条件の行)
    outputs = [run_doctor()[1], run_doctor("--production")[1]]
    short_key = FAKE_SECRET_KEY[:20]
    settings.SECRET_KEY = short_key
    monkeypatch.delenv(PARTNER_ENV)
    settings.DEBUG = True
    outputs.append(run_doctor()[1])
    outputs.append(run_doctor("--production")[1])

    text = "\n".join(outputs)
    assert "直す " in text
    for value in (FAKE_SECRET_KEY, short_key, FAKE_ENV_SECRET_KEY, FAKE_PARTNER_SECRET, FAKE_DB_PASSWORD, FAKE_AI_KEY):
        leaked = value in text
        assert not leaked, "出力に鍵の値が出た"
    env_value_leaked = "fake-env-value-that-must-not-appear" in text
    assert not env_value_leaked, "出力に .env の中身が出た"
    source = Path(doctor.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source


# ---------------------------------------------------------------- Cookie が HTTPS だけ (N69)


@pytest.mark.django_db
def test_cookie_row_only_in_production(site, settings):
    code, out = run_doctor()
    assert any("Cookie が HTTPS だけ" in line for line in good_lines(out))

    settings.SESSION_COOKIE_SECURE = False
    code, out = run_doctor()

    assert code == 1
    rows = [line for line in fix_lines(out) if "Cookie が HTTPS だけ" in line]
    assert len(rows) == 1
    # 直し方の文は 2 つの設定の名前を両方持つので、「が偽」まで含めて比べる。
    assert "SESSION_COOKIE_SECURE が偽" in rows[0]
    assert "CSRF_COOKIE_SECURE が偽" not in rows[0]
    assert "docs/operations.md" in rows[0]

    settings.DEBUG = True
    code, out = run_doctor()
    assert "Cookie が HTTPS だけ" not in out
    code, out = run_doctor("--production")
    assert any("Cookie が HTTPS だけ" in line for line in fix_lines(out))


def test_scaffold_sets_https_cookies_only_in_production():
    import ast
    import string

    import seizou.scaffold

    text = (Path(seizou.scaffold.__file__).parent / "settings.py.tmpl").read_text(encoding="utf-8")
    tree = ast.parse(string.Template(text).safe_substitute(id="t", seizou_items=""))

    def assigned(nodes) -> dict:
        found = {}
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                found[node.targets[0].id] = node.value
        return found

    top = assigned(tree.body)
    assert isinstance(top.get("SESSION_COOKIE_HTTPONLY"), ast.Constant) and top["SESSION_COOKIE_HTTPONLY"].value is True
    for name in ("SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SECURE_PROXY_SSL_HEADER"):
        assert name not in top, name

    blocks = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "DEBUG"
    ]
    assert len(blocks) == 1
    inner = assigned(blocks[0].body)
    assert ast.literal_eval(inner["SESSION_COOKIE_SECURE"]) is True
    assert ast.literal_eval(inner["CSRF_COOKIE_SECURE"]) is True
    assert ast.literal_eval(inner["SECURE_PROXY_SSL_HEADER"]) == ("HTTP_X_FORWARDED_PROTO", "https")


@pytest.mark.django_db
def test_settings_check_takes_empty_brand_color_and_industry_as_unset(settings):
    # 既定の空の字 (主色なし・業種なし) は「良い」。N77 の 10 は、ここが「直す」になって落ちた
    settings.SEIZOU = {}
    assert [(row.ok, row.label) for row in doctor.check_settings()] == [(True, "会社の選べる設定")]

    # 値があるときだけ形を見る (主色は # と 16 進の 6 桁、業種は 40 字まで)
    settings.SEIZOU = {"brand_color": "red", "industry": "業" * 41}
    fixes = {row.label: row.fix for row in doctor.check_settings() if not row.ok}
    assert set(fixes) == {"設定 brand_color", "設定 industry"}
    assert "brand_color は # と 16 進の 6 桁" in fixes["設定 brand_color"]
    assert "industry (業種) は 40 字まで" in fixes["設定 industry"]

    settings.SEIZOU = {"brand_color": "#15803D", "industry": "機械加工"}
    assert [(row.ok, row.label) for row in doctor.check_settings()] == [(True, "会社の選べる設定")]
