"""立ち上げの確かめ (点検)。命令 seizou_doctor の中身。

置き場を読むだけで、何も書かない。鍵の値は出さない (有るか無いかだけ)。
本番の条件の行は DEBUG が偽のとき (か --production) だけ見る。
環境変数は読まない。つながりの鍵の有無は Connection.has_secret() で見て、.env は権限だけを見る (中身は読まない)。
点検の関数は 1 つにつき 1 つの物を見て、Row の並びを返す。どの関数も例外を外に出さない (run が行にする)。
部品 (connect など) を import しない。つながりの model は apps.get_model で関数の中で引く。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.checks import ERROR, run_checks
from django.core.checks.security.base import (
    SECRET_KEY_INSECURE_PREFIX,
    SECRET_KEY_MIN_LENGTH,
    SECRET_KEY_MIN_UNIQUE_CHARACTERS,
)
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor

from seizou.core import backup, conf, media, middleware, services

#: ほかの PC から開けない名前 (ALLOWED_HOSTS にこれだけなら、本番では直す)
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
#: nginx の断片の中の internal; と alias の行 (# の行は見ない)
INTERNAL_PATTERN = re.compile(r"^\s*internal\s*;", re.MULTILINE)
ALIAS_PATTERN = re.compile(r'alias\s+"?([^";]+?)/?"?\s*;')
#: 設定の値が外れたときの直し方の後ろ半分
SETTING_FIX = "answers.yaml の settings と settings.py の SEIZOU を直す (画面で上書きした値なら設定の画面で直す)。"
#: Cookie が http でも送られるときの直し方
COOKIE_FIX = (
    "settings.py の if not DEBUG: の中に SESSION_COOKIE_SECURE = True と CSRF_COOKIE_SECURE = True があるかを見て、"
    "無ければ足し、nginx で https を受ける (seizou の docs/operations.md の「版を上げる手順」)。"
    "LAN の中だけで http で開く置き場は、この 2 行を消すので、この行は直すのまま残る。"
)


@dataclass(frozen=True)
class Row:
    """点検の 1 行。ok が偽の行だけ fix (直し方) を持つ。"""

    ok: bool
    label: str
    detail: str
    fix: str = ""


def _good(label, detail) -> Row:
    return Row(True, label, detail)


def _bad(label, detail, fix) -> Row:
    return Row(False, label, detail, fix)


def is_production() -> bool:
    """本番の条件か (DEBUG が偽 = 置き場の .env の DEBUG=0)。"""
    return not settings.DEBUG


def _base_dir() -> Path | None:
    base = getattr(settings, "BASE_DIR", None)
    return Path(base) if base else None


def _masked(text: str) -> str:
    """DB の設定のパスワードが文に含まれていれば *** に置き換える。"""
    password = str((settings.DATABASES.get("default") or {}).get("PASSWORD") or "")
    return text.replace(password, "***") if password else text


def _database_error_row(label, exc) -> Row:
    first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
    return _bad(
        label,
        f"データベースにつながらない: {_masked(first)}",
        "settings.py と .env の DB の設定を確かめる。",
    )


def check_secret_key() -> list[Row]:
    label = "SECRET_KEY"
    try:
        key = getattr(settings, "SECRET_KEY", "") or ""
    except ImproperlyConfigured:
        key = ""
    strong = (
        len(key) >= SECRET_KEY_MIN_LENGTH
        and len(set(key)) >= SECRET_KEY_MIN_UNIQUE_CHARACTERS
        and not key.startswith(SECRET_KEY_INSECURE_PREFIX)
    )
    if strong:
        return [_good(label, "置いてある")]
    return [
        _bad(
            label,
            "無いか、短いか、字の種類が少ないか、Django の仮の値の頭で始まる",
            "新しい値を作って .env の SECRET_KEY に置く (seizou new が作る物と同じ作り方)。値は画面と記録に出さない。",
        )
    ]


def check_env_file() -> list[Row]:
    label = ".env の権限"
    base = _base_dir()
    if base is None:
        return []
    path = base / ".env"
    if not path.exists():
        return [_good(label, "無い (鍵は環境変数から読んでいる)")]
    if os.name == "nt":
        return [_good(label, "ある (Windows では権限を見ない)")]
    mode = path.stat().st_mode
    if mode & 0o077 == 0:
        return [_good(label, "ある (持ち主だけが読める)")]
    return [_bad(label, f"ほかの利用者も読める (権限 {oct(mode & 0o777)})", f"chmod 600 {path} で持ち主だけが読める権限にする。")]


def check_debug(production: bool) -> list[Row]:
    label = "開発の設定 (DEBUG)"
    if not settings.DEBUG:
        return [_good(label, "本番 (0)")]
    if production:
        return [_bad(label, "開発 (1)", ".env の DEBUG を 0 にする。")]
    return [_good(label, "開発 (1)。本番にするときは .env の DEBUG=0 にして、もう 1 度 seizou_doctor を打つ")]


def check_dev_auto_login() -> list[Row]:
    label = "開発機の自動ログイン"
    name = middleware.DEV_AUTO_LOGIN_SETTING
    if getattr(settings, name, False):
        return [_bad(label, f"{name} が真", f".env の {name} を 0 にするか行を消す (本番では動かさない)。")]
    return [_good(label, "使わない")]


def check_allowed_hosts() -> list[Row]:
    label = "ALLOWED_HOSTS"
    names = [host for host in settings.ALLOWED_HOSTS if host not in LOCAL_HOSTS]
    if names:
        return [_good(label, ", ".join(names))]
    return [
        _bad(
            label,
            "この PC の名前 (localhost など) だけ",
            ".env の ALLOWED_HOSTS に、このシステムを開く名前 (サーバーの名前か IP。, 区切り) を足す。",
        )
    ]


def check_migrations() -> list[Row]:
    label = "migrate の漏れ"
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except DatabaseError as exc:
        return [_database_error_row(label, exc)]
    if plan:
        return [_bad(label, f"まだ当てていない migration が {len(plan)} 件", f"manage.py migrate を打つ ({len(plan)} 件)。")]
    return [_good(label, "無い")]


def check_django() -> list[Row]:
    label = "Django の起動の検査 (check)"
    errors = [message for message in run_checks() if message.level >= ERROR and not message.is_silenced()]
    if errors:
        return [_bad(label, f"誤り {len(errors)} 件", f"manage.py check を打って中身を見る (誤り {len(errors)} 件)。")]
    return [_good(label, "誤り無し")]


def _writable(label, path: Path, later: str, fix: str) -> Row:
    """フォルダがあれば書けるか、無ければ親のフォルダに書けるか (何も作らない)。"""
    if path.is_dir():
        if os.access(path, os.W_OK):
            return _good(label, f"{path} (書ける)")
        return _bad(label, f"{path} に書けない", fix)
    if path.exists():
        return _bad(label, f"{path} がフォルダでない", fix)
    parent = path.parent
    if parent.is_dir() and os.access(parent, os.W_OK):
        return _good(label, f"{path} はまだ無い。{later}")
    return _bad(label, f"{path} が無く、親のフォルダにも書けない", fix)


def check_media_root() -> list[Row]:
    label = "上げたファイルの置き場 (MEDIA_ROOT)"
    root = getattr(settings, "MEDIA_ROOT", "") or ""
    if not root:
        return [_bad(label, "MEDIA_ROOT が空", "settings.py に MEDIA_ROOT を書く。")]
    path = Path(root)
    fix = f"フォルダ {path} を作って、サーバーを動かす利用者が書ける権限にする。"
    return [_writable(label, path, "最初の upload で作られる", fix)]


def check_backups_dir() -> list[Row]:
    label = "控えの置き場 (backups/)"
    try:
        path = backup.default_dir()
    except backup.BackupError:
        return [_bad(label, "settings.py に BASE_DIR が無い", "settings.py に BASE_DIR を書く (seizou new が書く settings.py にはある)。")]
    fix = f"フォルダ {path} を作って、seizou_backup を打つ利用者が書ける権限にする。"
    return [_writable(label, path, "最初の控えで作られる", fix)]


def check_css() -> list[Row]:
    label = "CSS"
    if finders.find("css/app.css") is None:
        return [
            _bad(label, "seizou の css/app.css が見つからない", "seizou を入れ直す (uv sync --locked か pip install --force-reinstall seizou)。")
        ]
    return [_good(label, "css/app.css がある")]


def check_collectstatic() -> list[Row]:
    label = "collectstatic"
    root = getattr(settings, "STATIC_ROOT", None)
    if not root:
        return [_bad(label, "STATIC_ROOT が空", "settings.py に STATIC_ROOT を書く。")]
    if not (Path(root) / "css" / "app.css").is_file():
        return [
            _bad(
                label,
                f"{root} に css/app.css が無い",
                "manage.py collectstatic --no-input を打つ "
                "(DEBUG=0 では runserver が静的ファイルを送らないので、nginx が STATIC_ROOT を配る)。",
            )
        ]
    return [_good(label, f"{root} に集めてある")]


def check_file_delivery() -> list[Row]:
    label = "上げたファイルの配り方"
    mode = media.delivery()
    if mode not in media.DELIVERIES:
        return [_bad(label, f"{media.SETTING} の値「{mode}」は使えない", media.ADVICE_BAD_VALUE)]
    if mode == media.DELIVERY_DJANGO:
        return [_good(label, "Django が送る")]
    base = _base_dir()
    snippet = base / media.NGINX_SNIPPET if base is not None else None
    if snippet is None or not snippet.is_file():
        return [
            _bad(
                label,
                f"nginx に渡す設定だが、{media.NGINX_SNIPPET} が無い",
                f"seizou new が書く {media.NGINX_SNIPPET} が無い。作り直すか、ひな形を写す。",
            )
        ]
    lines = snippet.read_text(encoding="utf-8").splitlines()
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    rows = []
    if INTERNAL_PATTERN.search(text) is None:
        rows.append(_bad(label, "断片に internal; が無い", "断片に internal; を戻す (無いと外からファイルが開ける)。"))
    media_root = Path(getattr(settings, "MEDIA_ROOT", "") or ".").resolve()
    match = ALIAS_PATTERN.search(text)
    if match is None or Path(match.group(1).strip()).resolve() != media_root:
        shown = match.group(1).strip() if match else "無い"
        rows.append(_bad(label, f"断片の alias ({shown}) が MEDIA_ROOT と違う", f"断片の alias を MEDIA_ROOT ({media_root}) に合わせる。"))
    if rows:
        return rows
    return [_good(label, "nginx に渡す (断片の alias は MEDIA_ROOT と同じ)")]


def check_settings() -> list[Row]:
    rows = []
    for key in conf.DEFAULTS:
        value = conf.setting(key)
        label = f"設定 {key}"
        detail = f"値の出所: {conf.setting_source(key)}"
        if key in conf.LOCKED_KEYS:
            if isinstance(conf.DEFAULTS[key], list) and not isinstance(value, list):
                rows.append(_bad(label, detail, f"{key} は並び (list) にしてください。{SETTING_FIX}"))
            continue
        try:
            services.validate_setting(key, value)
        except ValueError as exc:
            rows.append(_bad(label, detail, f"{exc} {SETTING_FIX}"))
    if rows:
        return rows
    return [_good("会社の選べる設定", f"{len(conf.DEFAULTS)} の鍵の値が選べる値の中")]


def _connections() -> list | None:
    """つながりの行 (connect が無い会社と、表が無いときは None)。"""
    if not apps.is_installed("seizou.connect"):
        return None
    model = apps.get_model("connect", "Connection")
    try:
        return list(model._default_manager.select_related("partner"))
    except DatabaseError:
        return None


def check_connection_secrets() -> list[Row]:
    links = _connections()
    if links is None:
        return []
    if not links:
        return [_good("つながりの鍵", "つながりは無い")]
    rows = []
    for link in links:
        label = f"つながり {link} の鍵"
        if link.has_secret():
            rows.append(_good(label, f"ある ({link.secret_env})"))
        else:
            rows.append(
                _bad(
                    label,
                    f"無い ({link.secret_env})",
                    f".env に {link.secret_env}= の行を足す "
                    f"(値は manage.py seizou_secret {link.secret_env} で作り、相手の会社と同じ値にする)。",
                )
            )
    return rows


def check_connection_urls() -> list[Row]:
    links = _connections() or []
    rows = []
    for link in links:
        if link.direction != link.DIRECTION_SEND or not link.url:
            continue
        label = f"つながり {link} の受け口の URL"
        if link.url.startswith("https://"):
            rows.append(_good(label, "https"))
        else:
            rows.append(
                _bad(
                    label,
                    "https:// で始まらない",
                    "つながりの受け口の URL を https:// にする (同じ PC の試しの http://127.0.0.1 は本番では使わない)。",
                )
            )
    return rows


def check_cookies() -> list[Row]:
    label = "Cookie が HTTPS だけ"
    off = [name for name in ("SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE") if not getattr(settings, name, False)]
    if not off:
        return [_good(label, "セッションと CSRF の Cookie を HTTPS の道でだけ送る")]
    return [_bad(label, f"{'・'.join(off)} が偽 (http でも送る)", COOKIE_FIX)]


def _safe(label, check) -> list[Row]:
    """点検の関数を 1 つ呼ぶ。例外は、その関数の行を「直す」にして次へ進む。"""
    try:
        return list(check())
    except DatabaseError as exc:
        return [_database_error_row(label, exc)]
    except OSError as exc:
        return [_bad(label, f"読めない ({exc.strerror or type(exc).__name__})", "道と権限を確かめる。")]
    except Exception as exc:
        return [
            _bad(
                label,
                f"点検そのものが失敗した ({type(exc).__name__})",
                "settings.py と置き場を確かめる。直らなければ seizou の不具合として知らせる。",
            )
        ]


def run(*, production: bool | None = None) -> list[Row]:
    """点検の行を、表の順につないで返す。production が None なら is_production() で決める。"""
    if production is None:
        production = is_production()
    checks = [
        ("SECRET_KEY", check_secret_key, False),
        (".env の権限", check_env_file, False),
        ("開発の設定 (DEBUG)", lambda: check_debug(production), False),
        ("開発機の自動ログイン", check_dev_auto_login, True),
        ("ALLOWED_HOSTS", check_allowed_hosts, True),
        ("migrate の漏れ", check_migrations, False),
        ("Django の起動の検査 (check)", check_django, False),
        ("上げたファイルの置き場 (MEDIA_ROOT)", check_media_root, False),
        ("控えの置き場 (backups/)", check_backups_dir, False),
        ("CSS", check_css, False),
        ("collectstatic", check_collectstatic, True),
        ("上げたファイルの配り方", check_file_delivery, False),
        ("会社の選べる設定", check_settings, False),
        ("つながりの鍵", check_connection_secrets, False),
        ("つながりの受け口の URL", check_connection_urls, True),
        ("Cookie が HTTPS だけ", check_cookies, True),
    ]
    rows = []
    for label, check, production_only in checks:
        if production_only and not production:
            continue
        rows.extend(_safe(label, check))
    return rows
