"""立ち上げの確かめ (点検)。命令 seizou_doctor の中身。

置き場を読むだけで、何も書かない。鍵の値は出さない (有るか無いかだけ)。
本番の条件の行は DEBUG が偽のとき (か --production) だけ見る。
環境変数は読まない。つながりの鍵の有無は Connection.has_secret() で見て、.env は権限だけを見る (中身は読まない)。
点検の関数は 1 つにつき 1 つの物を見て、Row の並びを返す。どの関数も例外を外に出さない (run が行にする)。
部品 (connect など) を module の頭で import しない。つながりの model は apps.get_model で、作り物の利用者の合言葉は accounts の demo_seed を import_module で、関数の中で引く。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model, hashers
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
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from seizou.core import backup, conf, media, middleware, rules, services
from seizou.core.access import KIND_TITLES as ACCESS_TITLES

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
#: 開発の設定 (DEBUG が真) のまま --production で点検したときの、Cookie が http でも送られるときの直し方
COOKIE_FIX_DEBUG = (
    "開発の設定 (.env の DEBUG=1) では、settings.py の if not DEBUG: の中の 2 行が働かない。"
    "本番にするときに .env の DEBUG を 0 にして、点検し直す "
    "(0 にしても直すのままなら、settings.py にその 2 行があるかを見る。seizou の docs/operations.md の「本番の守り」)。"
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


#: MEDIA_ROOT が置き場そのものかその親のときの直し方
MEDIA_ROOT_PLACE_FIX = (
    'settings.py の MEDIA_ROOT を、置き場の中のフォルダ (seizou new が書く形は MEDIA_ROOT = BASE_DIR / "media") にする。'
    "直すまで、seizou_backup と seizou_restore は何もせずに止まる。"
)


def check_media_root_place() -> list[Row]:
    label = "上げたファイルの置き場の場所 (MEDIA_ROOT)"
    base = _base_dir()
    root = getattr(settings, "MEDIA_ROOT", "") or ""
    if base is None or not root:
        return []
    problem = backup.media_root_problem(base, root)
    if problem:
        return [_bad(label, problem, MEDIA_ROOT_PLACE_FIX)]
    return [_good(label, "置き場そのものでも、その親でもない")]


def check_backups_dir() -> list[Row]:
    label = "控えの置き場 (backups/)"
    try:
        path = backup.default_dir()
    except backup.BackupError:
        return [_bad(label, "settings.py に BASE_DIR が無い", "settings.py に BASE_DIR を書く (seizou new が書く settings.py にはある)。")]
    fix = f"フォルダ {path} を作って、seizou_backup を打つ利用者が書ける権限にする。"
    return [_writable(label, path, "最初の控えで作られる", fix)]


#: 戻せるかを確かめてから (確かめた記録が無ければ一番古い控えから) この日数を過ぎると、本番の条件で直すにする
RESTORE_CHECK_DAYS = 90


def check_restore_checks(production: bool) -> list[Row]:
    """控えから戻せるかを最後に確かめた日 (seizou_restore --verify の記録 backups/restore-checks.jsonl の最後の成功の行)。

    直すにするのは、本番の条件で、控えが 1 つ以上あり、最後に確かめた日 (確かめた記録が無ければ一番古い控えの日) から
    90 日より古いときだけ。ほかは良いの行に注意の文を書く (点検の行は良い / 直すの 2 つだけ。check_demo_passwords と同じ形)。
    作ったばかりの置き場 (控えが無いか、新しい控えだけ) では直すにしない。控えの日は控えのフォルダの名前 (日時) から読む。
    読めない記録の行は飛ばし、例外を外に出さない (run の _safe も包む)。
    訳: 控えを取っていても戻せるかを確かめていなければ、壊れた日に戻せないと分かるので、確かめた日を点検で見せるため。出所 = エンタープライズ化の調べを受けた作者の決め。変えてよい条件 = 点検に注意の段ができたとき、90 日の前にも注意を出す。
    """
    label = "控えから戻せるかの確かめ"
    try:
        parent = backup.default_dir()
    except backup.BackupError:
        return []
    names = []
    if parent.is_dir():
        for folder in parent.iterdir():
            if folder.is_dir() and backup.NAME_PATTERN.match(folder.name) and (folder / backup.MANIFEST).is_file():
                names.append(folder.name)
    taken = []
    for name in sorted(names):
        try:
            taken.append((datetime.strptime(name[:15], "%Y%m%d-%H%M%S"), name))
        except ValueError:
            continue
    if not taken:
        return [_good(label, "控えはまだありません")]
    now = datetime.now()
    last = backup.last_restore_check(parent)
    if last is None:
        days = (now - taken[0][0]).days
        detail = f"まだ確かめていません (一番古い控えから {days} 日)"
    else:
        days = (now - last).days
        detail = f"最後に確かめた日 {last.date().isoformat()} ({days} 日前)"
    if days <= RESTORE_CHECK_DAYS:
        return [_good(label, f"{detail}。{RESTORE_CHECK_DAYS} 日を過ぎると、本番の条件では直すにします")]
    fix = f"manage.py seizou_restore {taken[-1][1]} --verify を打つ (seizou の docs/operations.md の「戻す (復元)」)。"
    if production:
        return [_bad(label, detail, fix)]
    return [_good(label, f"{detail}。{RESTORE_CHECK_DAYS} 日を過ぎています。本番にする前に、{fix}")]


#: 最後の控えが古いとみなす時間・ディスクの空きの下限 (バイト)・一番新しい控えの大きさの何倍の空きを要るとみなすか
BACKUP_MAX_AGE_HOURS = 36
DISK_FREE_MIN_BYTES = 1024**3
DISK_FREE_BACKUP_TIMES = 2
#: 最後の控えとディスクの空きの直し方
LAST_BACKUP_FIX = "manage.py seizou_backup を打ち、cron の行を確かめる (seizou の docs/operations.md の「毎日の控え」)。"
DISK_FREE_FIX = "古い控えを seizou_backup --keep で消すか、ディスクを広げる (seizou の docs/operations.md の「毎日の控え」)。"
#: GB の字にするときの 1 GB のバイト数
_GB = 1024**3


def site_created_at() -> datetime | None:
    """置き場を作った時刻の代わり = django_migrations の一番早い applied (seizou new が migrate を打った時刻)。

    表が無ければ None。ORM で読むだけ。DB の誤りは外に出す (check_last_backup が直すにする)。試験が差し替える。
    """
    recorder = MigrationRecorder(connection)
    if not recorder.has_table():
        return None
    return recorder.migration_qs.order_by("applied").values_list("applied", flat=True).first()


def _hours_since(moment: datetime) -> float:
    """moment から今までの時間。tz の有る時刻 (django_migrations) は timezone.now() と、無い時刻 (控えの名前) は置き場の時計と比べる。"""
    now = timezone.now() if timezone.is_aware(moment) else datetime.now()
    return (now - moment).total_seconds() / 3600


def check_last_backup() -> list[Row]:
    """最後の控え (置き場の backups/ の、退避でない一番新しい控え) が BACKUP_MAX_AGE_HOURS より新しいか。本番の条件だけ。

    控えが無いときは、置き場を作った時刻 (site_created_at) から BACKUP_MAX_AGE_HOURS の内なら良い。起点が読めなければ直す。
    訳: 毎晩の控えの失敗・古さとディスクの空きの足りなさを、点検が知らせるため。36 時間は毎晩 2 時の控えが 1 回抜けても朝の点検で気づく幅、作ったばかりの置き場 (審査用に作り直す置き場を含む) で直すにしないため、起点は置き場を作った時の migrate の時刻 (作った日時を持つ所が無いので、版を上げても動かない一番早い行を使う)。1GB と 2 倍は、次の控えを 1 つ取れる目安。出所 = エンタープライズ化の調べを受けた作者の決め。変えてよい条件 = 置き場に作った日時を持つ所ができたとき (起点を替える)、または毎晩の控えの時刻と回数を変えたとき (幅を替える)。
    """
    label = "最後の控え"
    try:
        parent = backup.default_dir()
    except backup.BackupError:
        return []
    newest = backup.newest_backup(parent)
    if newest is not None:
        hours = _hours_since(backup.name_time(newest["name"]))
        shown = f"{newest['name']} ({max(0, int(hours))} 時間前)"
        if hours < BACKUP_MAX_AGE_HOURS:
            return [_good(label, shown)]
        return [_bad(label, f"{shown}。{BACKUP_MAX_AGE_HOURS} 時間より古い", LAST_BACKUP_FIX)]
    try:
        created = site_created_at()
    except DatabaseError:
        created = None
    if created is None:
        return [_bad(label, "控えが無い (置き場を作った時刻も読めない)", LAST_BACKUP_FIX)]
    hours = _hours_since(created)
    if hours < BACKUP_MAX_AGE_HOURS:
        return [_good(label, f"まだ最初の控えの前 (置き場を作ってから {max(0, int(hours))} 時間)")]
    return [_bad(label, f"控えが無い (置き場を作ってから {int(hours)} 時間)", LAST_BACKUP_FIX)]


def check_disk_free() -> list[Row]:
    """控えの置き場 (無ければ置き場) のディスクの空きが、DISK_FREE_MIN_BYTES と一番新しい控えの DISK_FREE_BACKUP_TIMES 倍の大きい方以上か。本番の条件だけ。

    訳: 毎晩の控えの失敗・古さとディスクの空きの足りなさを、点検が知らせるため。36 時間は毎晩 2 時の控えが 1 回抜けても朝の点検で気づく幅、作ったばかりの置き場 (審査用に作り直す置き場を含む) で直すにしないため、起点は置き場を作った時の migrate の時刻 (作った日時を持つ所が無いので、版を上げても動かない一番早い行を使う)。1GB と 2 倍は、次の控えを 1 つ取れる目安。出所 = エンタープライズ化の調べを受けた作者の決め。変えてよい条件 = 置き場に作った日時を持つ所ができたとき (起点を替える)、または毎晩の控えの時刻と回数を変えたとき (幅を替える)。
    """
    label = "ディスクの空き"
    try:
        parent = backup.default_dir()
    except backup.BackupError:
        return []
    place = parent if parent.is_dir() else parent.parent
    free = shutil.disk_usage(place).free
    newest = backup.newest_backup(parent)
    size = newest["size"] if newest is not None else 0
    need = max(DISK_FREE_MIN_BYTES, size * DISK_FREE_BACKUP_TIMES)
    detail = f"空き {free / _GB:.1f} GB (要る目安 {need / _GB:.1f} GB)"
    if free < need:
        return [_bad(label, detail, DISK_FREE_FIX)]
    return [_good(label, detail)]


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
    """セッションと CSRF の Cookie を HTTPS の道でだけ送るか。偽の設定があれば直すで、DEBUG が真なら直し方を DEBUG を 0 にする案内にする。

    訳: 開発の設定のまま --production で点検すると、settings.py に 2 行があっても直すになり、「無ければ足し」の直し方では
    次にすることが分からないため、DEBUG が 1 のときは DEBUG を 0 にする案内を出す。出所 = 精査 Q4 (2026-11-10) のつまずく所 9。
    変えてよい条件 = 雛形の Cookie の塊の形 (if not DEBUG:) が変わったとき。
    """
    label = "Cookie が HTTPS だけ"
    off = [name for name in ("SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE") if not getattr(settings, name, False)]
    if not off:
        return [_good(label, "セッションと CSRF の Cookie を HTTPS の道でだけ送る")]
    fix = COOKIE_FIX_DEBUG if settings.DEBUG else COOKIE_FIX
    return [_bad(label, f"{'・'.join(off)} が偽 (http でも送る)", fix)]


#: パスワードの決まりが settings に無いときの直し方 (開発の記録 docs/nights/20261108/N354.md)
PASSWORD_VALIDATORS_FIX = (
    "settings.py の import の並びに from seizou.core.passwords import VALIDATORS as AUTH_PASSWORD_VALIDATORS を足し、"
    "サーバーを立て直す (seizou の docs/operations.md の「版を上げる手順」)。"
)


def check_password_validators() -> list[Row]:
    """settings の AUTH_PASSWORD_VALIDATORS が空でないか (本番の条件のときだけ見る。Cookie の行と同じ扱い)。

    訳: 手引きが勧める manage.py changepassword は settings の AUTH_PASSWORD_VALIDATORS で確かめるので、空だと 1 字のパスワードも通る。
    この版より前に seizou new で作った置き場は settings.py にこの行が無いので、足りない 1 行を知らせる。
    出所 = 作者の決め (accounts.services.password_validators の 4 つ) と、エンタープライズ化の調べ 第一弾が挙げた
    OWASP ASVS 5.0 の 6.2 (作業場に写しが無く、条の中身は未確認)。
    変えてよい条件 = 点検に中間の段 (注意) ができたとき、本番の外でも見るかを決め直す。
    """
    label = "パスワードの決まり"
    validators = getattr(settings, "AUTH_PASSWORD_VALIDATORS", None)
    if not validators:
        return [_bad(label, "AUTH_PASSWORD_VALIDATORS が空 (changepassword で 1 字のパスワードも通る)", PASSWORD_VALIDATORS_FIX)]
    return [_good(label, f"確かめ {len(validators)} 個")]

#: 雛形の MIDDLEWARE の、htmx の部分の要求でログインが切れたときの 1 行 (点検の「settings.py の塊」が見る)
HTMX_LOGIN_MIDDLEWARE = "seizou.core.middleware.HtmxLoginMiddleware"
#: settings.py の塊が足りないときの直し方の後ろ半分
SETTINGS_BLOCKS_FIX = "を足し、サーバーを立て直す (seizou の docs/operations.md の「版を上げる手順」)。"


def check_settings_blocks() -> list[Row]:
    """settings.py の塊のうち、この版より前に作った置き場に無いことがある 2 つ (LOGGING と HtmxLoginMiddleware の行)。

    LOGGING は Django の既定が空の辞書なので、空なら無いと見る (中身の形は見ない)。run の表で本番の条件の行だけにする。
    AUTH_PASSWORD_VALIDATORS は見ない (別の行が見る)。
    訳: 版を上げても置き場の settings.py は書き換わらないので、雛形に足した塊が無いままの置き場を点検で見つけるため。
    LOGGING が無いと本番の 500 の誤りと seizou の記録が残らず、HtmxLoginMiddleware が無いと、ログインが切れた後の
    部分の要求で画面の一部にログインの画面が入る。出所 = エンタープライズ化の調べを受けた作者の決め
    (開発の記録 docs/nights/20261108/N362.md)。変えてよい条件 = 雛形の塊が増えたら見る物を足す。
    """
    label = "settings.py の塊"
    missing = []
    adds = []
    if not getattr(settings, "LOGGING", None):
        missing.append("LOGGING が無い (500 の誤りの記録が残らない)")
        adds.append("LOGGING の 2 行 (import と LOGGING = default_logging())")
    if HTMX_LOGIN_MIDDLEWARE not in list(getattr(settings, "MIDDLEWARE", None) or ()):
        missing.append("HtmxLoginMiddleware が無い (ログインが切れた後の部分の要求で、画面の一部がログインの画面になる)")
        adds.append("MIDDLEWARE の HtmxLoginMiddleware の 1 行")
    if not missing:
        return [_good(label, "LOGGING と HtmxLoginMiddleware の行がある")]
    return [_bad(label, "・".join(missing), f"settings.py に {'と'.join(adds)}{SETTINGS_BLOCKS_FIX}")]


#: 権限の判定が記録だけのままのときの直し方 (決め 11-07 22:3x・23:3x)
ACCESS_CONTROL_FIX = "断られる人がいないかを seizou.access の記録で確かめ終えたら、設定の画面の「権限の判定」で on にする。"


def _access_detail(modes: dict) -> str:
    """権限の判定の 3 つの状態の字 (記録だけ → 使わない → 有効 の順に、あるものだけ「。」でつなぐ)。"""
    titles = {mode: [ACCESS_TITLES[kind] for kind, value in modes.items() if value == mode] for mode in conf.ACCESS_MODES}
    parts = []
    if titles["log"]:
        note = " (金額を伏せたつもりの人にも金額が見える)" if modes["money"] == "log" else ""
        parts.append(f"記録だけ: {'・'.join(titles['log'])}{note}")
    if titles["off"]:
        note = " (全員が金額を見る)" if modes["money"] == "off" else ""
        parts.append(f"使わない: {'・'.join(titles['off'])}{note}")
    if titles["on"]:
        parts.append(f"有効: {'・'.join(titles['on'])}")
    return "。".join(parts)


def check_access_control(production: bool) -> list[Row]:
    """権限の判定 (部署・編集の権限・金額の閲覧) が記録だけのままか。conf.access_control で読む (画面で上書きした値も同じに読む)。

    本番の条件で log が 1 つでもあれば直す。off は 1 人で使う会社が選んだ形として良いにし、detail に注を付ける。全部 on なら良い。
    開発の条件では良いにして、今の状態を 1 行で出す (check_demo_passwords の開発の行と同じ形)。
    訳: docs/rules.md の「立ち上げてすぐは記録だけ」の決めは残し、本番の条件 (DEBUG=0) で部署・編集・金額の閲覧の判定が「記録だけ」の
    ままなら、点検が「直す」を出し、終わりの番号 1 で終わる。記録だけで確かめている間に点検が失敗で終わるのは、確かめ終えたら
    有効にする合図として受け入れる。直し方の字には「断られる人がいないかを seizou.access の記録で確かめ終えたら on にする」と書き、
    docs/rules.md の「立ち上げてすぐは記録だけ」の文に「その間、本番の点検は直す 1 件で終わる」の 1 句を足し、試験の fixture
    (tests/test_core_doctor.py の site の SEIZOU) に access_control の on を足す。記録だけのままでは金額を見られない人の決め
    (21:0x) が働かないため。欠点として、記録だけの間はほかの「直す」が同じ失敗の番号に埋もれやすい。
    出所 = 本人の決め (11-07 22:3x・23:3x) と docs/settings.md の「選べる設定」の access_control の行。
    変えてよい条件 = 本人が既定そのものを「止める」に変えるか、点検に中間の段を作るか失敗にしない形に変えると決めたとき。
    """
    label = "権限の判定"
    modes = {kind: conf.access_control(kind) for kind in conf.DEFAULTS["access_control"]}
    detail = _access_detail(modes)
    if "log" not in modes.values():
        return [_good(label, detail)]
    if production:
        return [_bad(label, detail, ACCESS_CONTROL_FIX)]
    return [_good(label, f"{detail}。本番の条件では直すにします")]


#: 作り物の利用者が公開の合言葉のままのときの直し方 (決め 11-04 15:0x。開発の記録 docs/nights/20261104/N116.md)
DEMO_PASSWORD_FIX = "アカウント管理で変えてください。"


def check_demo_passwords(production: bool) -> list[Row]:
    """作り物の利用者 (seizou new --demo の genba・jimu・keiri) のうち、公開の合言葉のままの人 (決め 11-04 15:0x)。

    合言葉は docs/getting-started.md に書いてあるので、外から開ける置き場では誰でもその人で入れる。本番の条件では直す、
    開発の設定では良いの行に注意の文を書く (点検の行は良い / 直すの 2 つだけ。check_debug と同じ形)。
    照らし合わせは hashers.check_password に setter を渡さずに呼ぶ (ハッシュを作り直して保存しない = 何も書かない)。
    """
    label = "作り物の利用者の合言葉"
    if not apps.is_installed("seizou.accounts"):
        return []
    demo_seed = import_module("seizou.accounts.demo_seed")
    ids = [login_id for login_id, *_ in demo_seed.ACCOUNTS]
    users = {user.username: user for user in get_user_model()._default_manager.filter(username__in=ids, is_active=True)}
    public = [i for i in ids if i in users and hashers.check_password(demo_seed.DEMO_PASSWORD, users[i].password)]
    if not public:
        return [_good(label, "公開の合言葉のままの作り物の利用者はいない")]
    detail = f"作り物の利用者 {'・'.join(public)} が公開の合言葉のままです"
    if production:
        return [_bad(label, detail, DEMO_PASSWORD_FIX)]
    return [_good(label, f"{detail}。外から開ける置き場にする前に、{DEMO_PASSWORD_FIX}")]


#: 置き場に置く、AI が守る決まり (seizou new と seizou ai-docs が書く 2 つ。開発の記録 docs/nights/20261105/N165.md・docs/nights/20261105/N170.md)
AI_DOCS = ("CLAUDE.md", "AGENTS.md")


def check_ai_docs() -> list[Row]:
    """置き場の AI が守る決まり (CLAUDE.md・AGENTS.md)。決め 11-06 00:0x。開発の記録 docs/nights/20261106/N173.md

    無くても止めない (良いの行に勧めの文を書く。check_debug の開発の行と同じ形)。片方だけ無いときは、
    seizou ai-docs が既にある方を見て止まるので、--force を添える。
    """
    label = "AI が守る決まり"
    base = _base_dir()
    if base is None:
        return []
    missing = [name for name in AI_DOCS if not (base / name).is_file()]
    if not missing:
        return [_good(label, "CLAUDE.md と AGENTS.md がある")]
    if len(missing) == len(AI_DOCS):
        return [_good(label, f"AI が守る決まり (CLAUDE.md・AGENTS.md) がありません。seizou ai-docs {base} で置けます。")]
    return [_good(label, f"{missing[0]} がありません。seizou ai-docs {base} --force で 2 つとも書き直せます (人が直した方も書き直す)。")]


RULES_LABEL = "決まりの検査 (会社の置き場)"
RULES_FIX = (
    "開発の起動 (DEBUG=1) で全面の知らせを見るか、seizou_check <置き場> で一覧を出し、"
    "/seizou:fix で会社の置き場の側だけを直す。"
)
RULES_ERROR_FIX = "seizou_check <置き場> を打って止まる所を見る。直らなければ seizou の不具合として知らせる。"
#: 直すの行に出す、最初の違反の決まりの文の頭の字数
RULES_MESSAGE_HEAD = 40


def _rules_place(path: str, base: Path) -> str:
    """違反のファイルを、置き場から見た道で返す (置き場の外なら渡された道のまま)。"""
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path


def check_rules() -> list[Row]:
    """会社の置き場の決まりの検査 (R13〜R19・R22)。違反 0 なら良い、1 件でもあれば直す。黙らせた物は数だけ添える。

    検査の対象は rules.site_paths() (会社の置き場の側だけ。部品の中は CI が見る)。判定は rules.check のまま。
    文には決まりの番号・置き場から見たファイルと行・決まりの文の頭だけを出し、コードの行の中身 (鍵の値や利用者の入力が入りうる) は出さない。
    検査そのものが例外で止まったら、例外の種類だけを出して直すにする (例外の文は出さない)。
    訳: 起動のときの決まりの検査は開発の設定 (DEBUG=1) でしか回らず、会社の置き場には CI が無いため、本番で打つ点検でも回して、
    お金と数の決まりを破る変更を本番の前に知らせる (cron で毎朝回す置き場では直すのメールで届く)。
    出所 = 11-08 の採点の崩す役の指摘を受けた依頼 (開発の記録 docs/nights/20261108/N375.md)。
    変えてよい条件 = 会社の置き場に CI ができたとき (この行を開発の条件だけにしてよい) か、決まりの検査の結果の形 (rules.Result) が変わったとき。
    """
    label = RULES_LABEL
    base = _base_dir()
    if base is None:
        return []
    try:
        result = rules.check(rules.site_paths())
    except Exception as exc:
        return [_bad(label, f"決まりの検査が回らなかった: {type(exc).__name__}", RULES_ERROR_FIX)]
    silenced = f"。黙らせた {result.silenced} 件" if result.silenced else ""
    if not result.violations:
        return [_good(label, f"違反 0 件 (走査 {result.scanned} 本){silenced}")]
    first = result.violations[0]
    head = first.message[:RULES_MESSAGE_HEAD]
    detail = (
        f"違反 {len(result.violations)} 件。最初の 1 件: {first.code} {_rules_place(first.path, base)}:{first.line} {head}"
        f"{silenced}"
    )
    return [_bad(label, detail, RULES_FIX)]


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
        ("上げたファイルの置き場の場所 (MEDIA_ROOT)", check_media_root_place, False),
        ("控えの置き場 (backups/)", check_backups_dir, False),
        ("控えから戻せるかの確かめ", lambda: check_restore_checks(production), False),
        ("最後の控え", check_last_backup, True),
        ("ディスクの空き", check_disk_free, True),
        ("CSS", check_css, False),
        ("collectstatic", check_collectstatic, True),
        ("上げたファイルの配り方", check_file_delivery, False),
        ("会社の選べる設定", check_settings, False),
        ("権限の判定", lambda: check_access_control(production), False),
        ("つながりの鍵", check_connection_secrets, False),
        ("つながりの受け口の URL", check_connection_urls, True),
        ("Cookie が HTTPS だけ", check_cookies, True),
        ("パスワードの決まり", check_password_validators, True),
        ("settings.py の塊", check_settings_blocks, True),
        ("作り物の利用者の合言葉", lambda: check_demo_passwords(production), False),
        ("AI が守る決まり", check_ai_docs, False),
        (RULES_LABEL, check_rules, False),
    ]
    rows = []
    for label, check, production_only in checks:
        if production_only and not production:
            continue
        rows.extend(_safe(label, check))
    return rows
