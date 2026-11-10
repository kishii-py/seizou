"""控え (バックアップ) と戻し (復元)。命令 seizou_backup・seizou_restore の中身。

控えは日時の名前のフォルダ 1 つ = DB の写し + media/ + answers.yaml + backup.json。.env (鍵) は控えない。
命令 seizou_backup の控えだけは、写した DB を integrity_check で確かめ、会社の作り込みの許しの一覧を site/ に写し、各ファイルの sha256 と当てた migration を backup.json に足す (戻す前の退避と練習モードの写しは遅くしない)。
DB の写しは SQLite の backup API (Django の生きている接続から。WAL でも整合が取れる)。
PostgreSQL は pg_dump を呼ぶ (無ければ案内して止まる)。
戻す前に同じ置き場に今の物を控える (戻しの戻し)。

命令は引数を読んで結果を書くだけで、処理はこの module に置く。表に書くのは戻しの backup API だけで、
ORM で表に書く処理は無い。部品 (connect など) を import しない。
戻せるかの確かめ (seizou_restore --verify) は、控えの DB の写しを一時のフォルダに写して読むだけで開き、
本番の DB と上げたファイルに触らない。結果は控えの置き場の restore-checks.jsonl に 1 行ずつ残す。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.recorder import MigrationRecorder

from seizou.core import conf

#: 中身の一覧のファイルの名前と、形の番号 (戻しは形の番号が同じ控えだけを読む)
MANIFEST = "backup.json"
FORMAT = 1
#: DB の種類 (connection.vendor の字) ごとの写しの名前
DB_FILES = {"sqlite": "db.sqlite3", "postgresql": "db.pgdump"}
#: 控えの中のフォルダとファイルの名前と、既定の置き場の名前 (BASE_DIR / "backups")
MEDIA_DIR = "media"
ANSWERS = "answers.yaml"
BACKUPS_DIR = "backups"
#: 戻す前の退避の名前の尻尾と、控えのフォルダの名前の形 (一覧はこの形の名前だけを拾う)
RETREAT_SUFFIX = "-before-restore"
NAME_PATTERN = re.compile(r"^\d{8}-\d{6}(-before-restore)?$")
#: 名前の頭の日時の形と字数 (new_name と同じ形。古さは名前で決め、mtime は見ない)
NAME_TIME_FORMAT = "%Y%m%d-%H%M%S"
NAME_TIME_LENGTH = 15
#: 命令 seizou_backup の控えだけが持つ、会社の作り込みの写しのフォルダと、写す物の許しの一覧 (置き場の直下。無い物は飛ばす。一覧の外は写さない)
#: checks.py は会社のポカよけ、.claude/ は Claude Code に .env・DB・控えを読ませない設定 (seizou new が書く)。置き場ごと失って作り直したときに消えないように写す
SITE_DIR = "site"
SITE_FILES = ("settings.py", "manage.py", "CLAUDE.md", "AGENTS.md", "README.md", "checks.py")
SITE_DIRS = ("fields", "templates", "static", "manual", "deploy", ".claude")
#: 作り込みのフォルダを写すときに外す物
SITE_IGNORE = ("__pycache__",)
#: integrity_check が通らないときに断りの文に添える行の数
INTEGRITY_LINES = 3
#: 命令 seizou_backup の控えの権限 (フォルダと、中のファイル。Windows では変えない)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

ENV_NOTICE = ".env (鍵) は控えに入れていません。別の安全な場所に写してください。"
#: pg_dump が無いときの案内 ({name} = DB の名前、{path} = 写しの道)。接続の鍵は入れない
PG_DUMP_GUIDE = (
    "pg_dump が見つからないので、控えを取りませんでした (何も書いていません)。"
    "PostgreSQL の控えは、pg_dump を入れてからもう 1 度打つか、この命令で取ってください: "
    "pg_dump --format=custom --no-owner --file {path} {name}"
)
#: PostgreSQL の戻しの案内 ({name} = DB の名前、{path} = 控えの写し、{media} = 控えの media/、{media_root} = MEDIA_ROOT)
PG_RESTORE_GUIDE = (
    "PostgreSQL の戻しは、この命令ではしません (何も変えていません)。サーバーを止めてから、"
    "この命令で戻してください: pg_restore --clean --if-exists --no-owner --dbname {name} {path}。"
    "上げたファイルは、控えの {media} を {media_root} に写してください。"
)
#: 戻せるかの確かめの記録の名前 (控えの置き場の中の 1 本のファイル。1 行 = 1 回の確かめ。一覧はフォルダだけを拾うので出ない)
RESTORE_CHECKS = "restore-checks.jsonl"
#: 確かめられない DB のときの断り ({saved} = 控えの DB の種類、{kind} = 今の DB の種類)
VERIFY_SQLITE_ONLY = (
    "この命令で確かめられるのは SQLite の控えだけです (控えの DB: {saved}、今の DB: {kind})。"
    "PostgreSQL の控えと置き場は、この命令では確かめられません。何も変えていません。"
)
#: quick_check の答えのうち、文に出す行の数
_QUICK_CHECK_LINES = 3

#: pg_dump の子の process に渡す、DATABASES の鍵と PostgreSQL の標準の環境変数の名前の組
_PG_ENV = {"HOST": "PGHOST", "PORT": "PGPORT", "USER": "PGUSER", "PASSWORD": "PGPASSWORD"}
#: pg_dump の失敗の文に添える、標準エラーの末尾の字数
_STDERR_TAIL = 300


class BackupError(Exception):
    """控えと戻しを止める理由 (日本語の文)。命令が CommandError にする。"""


def database_kind() -> str:
    """今の DB の種類 (connection.vendor = sqlite / postgresql / ほか)。試験が差し替える。"""
    return connection.vendor


def database_settings() -> dict:
    """今の DB の設定 (settings.DATABASES の default)。pg_dump の子の process の env に使う。試験が差し替える。"""
    return settings.DATABASES["default"]


def _base_dir() -> Path | None:
    base = getattr(settings, "BASE_DIR", None)
    return Path(base) if base else None


def _media_root() -> Path | None:
    root = getattr(settings, "MEDIA_ROOT", "") or ""
    return Path(root) if root else None


#: MEDIA_ROOT が置き場 (BASE_DIR) そのものか、その親を指すときの断りの文 ({media_root}・{base_dir} = resolve した道)
MEDIA_ROOT_PROBLEM = (
    "MEDIA_ROOT ({media_root}) が置き場 ({base_dir}) そのものか、その親を指しています。"
    "media/ のような置き場の中のフォルダにしてください。"
)


def media_root_problem(base_dir, media_root) -> str | None:
    """MEDIA_ROOT が置き場 (BASE_DIR) そのものか、その親 (祖先) を指すときの断りの文。ほかは None (読むだけ)。

    2 つの道を resolve してから比べる。置き場の中 (子孫) と、置き場の外の別の場所は None (断らない)。
    どちらかが空 (None・空の字) のときも None (空の MEDIA_ROOT と BASE_DIR の無い settings は点検の別の行が見る)。
    """
    if not base_dir or not media_root:
        return None
    base = Path(base_dir).resolve()
    media = Path(media_root).resolve()
    if base.is_relative_to(media):
        return MEDIA_ROOT_PROBLEM.format(media_root=media, base_dir=base)
    return None


def _refuse_bad_media_root() -> None:
    """控えと戻しの頭で呼ぶ。MEDIA_ROOT が置き場そのものかその親なら、何も書かず何も消さずに BackupError で止める。"""
    problem = media_root_problem(_base_dir(), _media_root())
    if problem:
        raise BackupError(problem + " 何も書かず、何も消していません。")


def default_dir() -> Path:
    """既定の控えの置き場 (置き場の backups/)。"""
    base = _base_dir()
    if base is None:
        raise BackupError("settings に BASE_DIR が無いので、--to で控えの置き場を指定してください。")
    return base / BACKUPS_DIR


def new_name(suffix="") -> str:
    """日時の名前 (置き場の時計の現地時刻) と尻尾。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S") + suffix


def current_version() -> str:
    """入っている seizou の版。配布物として入っていなければ空の字。"""
    try:
        return importlib.metadata.version("seizou")
    except importlib.metadata.PackageNotFoundError:
        return ""


def take_backup(parent: Path, *, suffix: str = "", full: bool = False) -> dict:
    """控えを 1 つ取り、backup.json の中身に folder (控えのフォルダの Path) を足した辞書を返す。

    途中で失敗したら、作りかけのフォルダを消してから例外を上げ直す (半端な控えを残さない)。
    full が真 (命令 seizou_backup だけ) のときは、写した SQLite の DB を integrity_check で確かめ (通らなければ BackupError)、
    会社の作り込みの許しの一覧 (SITE_FILES・SITE_DIRS) を site/ に写し、backup.json に sha256・migrations・site_files の鍵を足し、
    控えのフォルダを持ち主だけが読める権限にする。戻す前の退避と練習モードの写しは full を渡さない (今のまま)。
    PostgreSQL の写し (db.pgdump) は確かめない。
    訳: 壊れた控えを黙って残さず、戻した時に会社ごとの項目や帳票の形も一緒に戻せるようにするため。作り込みを入れるのは本人の決め (11-08 00:0x)。確かめと写しを命令の道だけにしたのは、生きた DB が壊れたときに戻す前の退避と練習モードが止まらないように (確かめ直しの (b))。.env は控えない決まり (backup.py の docstring・docs/operations.md の「毎日の控え」) のまま。出所 = エンタープライズ化の調べを受けた作者の決め。変えてよい条件 = 本人が控えの範囲を決め直したとき、または戻す前の退避にも確かめを掛けると決めたとき。
    """
    _refuse_bad_media_root()
    kind = database_kind()
    if kind not in DB_FILES:
        raise BackupError(f"この DB ({kind}) の控えは取れません。")
    pg_dump = None
    if kind == "postgresql":
        pg_dump = shutil.which("pg_dump")
        if pg_dump is None:
            name = str(database_settings().get("NAME", ""))
            path = Path(parent) / f"{new_name()}.pgdump"
            raise BackupError(PG_DUMP_GUIDE.format(name=name, path=path))
    folder = Path(parent) / new_name(suffix)
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise BackupError("同じ名前の控えがあります。1 秒待ってもう 1 度打ってください。") from exc
    except OSError as exc:
        raise BackupError(f"控えのフォルダを作れません: {folder} ({exc.strerror})") from exc
    try:
        if full:
            _make_private(folder)
        target = folder / DB_FILES[kind]
        if kind == "postgresql":
            _dump_postgres(target, pg_dump)
        else:
            _dump_sqlite(target)
            if full:
                _verify_sqlite_copy(target)
        media_root = _media_root()
        if media_root is not None and media_root.is_dir():
            shutil.copytree(media_root, folder / MEDIA_DIR)
        base = _base_dir()
        if base is not None and (base / ANSWERS).is_file():
            shutil.copyfile(base / ANSWERS, folder / ANSWERS)
        if full and base is not None:
            _copy_site(base, folder / SITE_DIR)
        manifest = manifest_for(kind, folder, full=full)
        text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        (folder / MANIFEST).write_text(text, encoding="utf-8")
        if full:
            _make_private(folder)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {**manifest, "folder": folder}


def _dump_sqlite(path: Path):
    """Django の生きている接続から、SQLite の backup API で整合の取れた写しを作る (ファイルを写さない)。"""
    connection.ensure_connection()
    target = sqlite3.connect(str(path))
    try:
        connection.connection.backup(target)
    finally:
        target.close()


def sqlite_integrity(path: Path) -> list[str]:
    """写した SQLite の DB を Python の標準の sqlite3 で開き、PRAGMA integrity_check の行を返す (表に書かない)。

    壊れていなければ ["ok"] の 1 行。開けない・DB でないときは sqlite3.DatabaseError が出る。試験が差し替える。
    標準の sqlite3 の接続の execute だけを使う (cursor を呼ばない。生きた DB には触らない)。
    """
    con = sqlite3.connect(str(path))
    try:
        return [str(row[0]) for row in con.execute("PRAGMA integrity_check")]
    finally:
        con.close()


def _verify_sqlite_copy(path: Path) -> None:
    """写した DB の確かめ。integrity_check が ok の 1 行でないか、開けなければ BackupError (呼び手が作りかけのフォルダを消す)。

    訳: 壊れた写しを控えとして黙って残さないため。出所 = エンタープライズ化の調べを受けた作者の決め。変えてよい条件 = 戻す前の退避にも確かめを掛けると本人が決めたとき (呼び手を増やす)。
    """
    try:
        rows = sqlite_integrity(path)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"控えの DB の確かめ (integrity_check) が通りません: {exc}。控えは残していません。") from exc
    if rows != ["ok"]:
        shown = " / ".join(rows[:INTEGRITY_LINES]) or "結果が空"
        raise BackupError(f"控えの DB の確かめ (integrity_check) が通りません: {shown}。控えは残していません。")


def _copy_site(base: Path, target: Path) -> None:
    """会社の作り込みの許しの一覧 (SITE_FILES・SITE_DIRS) のうち置き場にある物を target に写す。一覧の外 (.env・DB・media/ など) は写さない。"""
    target.mkdir()
    for name in SITE_FILES:
        source = base / name
        if source.is_file():
            shutil.copy2(source, target / name)
    for name in SITE_DIRS:
        source = base / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=shutil.ignore_patterns(*SITE_IGNORE))


def _make_private(folder: Path) -> None:
    """控えのフォルダと中のフォルダを 0o700、中のファイルを 0o600 にする (持ち主だけが読める)。Windows では何もしない。

    symlink は辿らない (写しの外の物の権限を変えない)。
    """
    if os.name == "nt":
        return
    for path in folder.rglob("*"):
        if path.is_symlink():
            continue
        path.chmod(PRIVATE_DIR_MODE if path.is_dir() else PRIVATE_FILE_MODE)
    folder.chmod(PRIVATE_DIR_MODE)


def _dump_postgres(path: Path, pg_dump: str):
    """pg_dump を呼んで写しを作る。接続の値は子の process の env にだけ渡し、文にしない。"""
    db = database_settings()
    env = dict(os.environ)
    for key, env_name in _PG_ENV.items():
        value = db.get(key)
        if value:
            env[env_name] = str(value)
    args = [pg_dump, "--format=custom", "--no-owner", "--file", str(path), str(db.get("NAME", ""))]
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = (result.stderr or "").strip()[-_STDERR_TAIL:]
        raise BackupError(f"pg_dump が失敗しました (終わりの番号 {result.returncode})。{tail}")


def _count_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for path in folder.rglob("*") if path.is_file())


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def file_hashes(folder: Path) -> dict:
    """控えのフォルダの下の全部のファイル (backup.json を除く) の sha256。鍵はフォルダからの相対の道 (/ 区切り)。"""
    folder = Path(folder)
    hashes = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        if relative == MANIFEST:
            continue
        hashes[relative] = _sha256(path)
    return hashes


def applied_migrations() -> dict:
    """app ごとの、一番新しく当てた migration の名前 (当てた時刻の順。同じ時刻なら名前の順)。ORM で django_migrations を読むだけ。"""
    newest = {}
    for (app, name), record in MigrationRecorder(connection).applied_migrations().items():
        key = (record.applied, name)
        if app not in newest or key > newest[app][0]:
            newest[app] = (key, name)
    return {app: newest[app][1] for app in sorted(newest)}


def manifest_for(kind, folder, *, full: bool = False) -> dict:
    """backup.json の中身。full (命令 seizou_backup の控え) のときだけ sha256・migrations・site_files の鍵を足す (FORMAT は同じ)。"""
    folder = Path(folder)
    manifest = {
        "format": FORMAT,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "database": kind,
        "db_file": DB_FILES[kind],
        "company_id": conf.company_id(),
        "seizou_version": current_version(),
        "media_files": _count_files(folder / MEDIA_DIR),
        "answers": (folder / ANSWERS).is_file(),
    }
    if full:
        manifest["sha256"] = file_hashes(folder)
        manifest["migrations"] = applied_migrations()
        manifest["site_files"] = _count_files(folder / SITE_DIR)
    return manifest


def read_manifest(folder: Path) -> dict:
    """backup.json を読む。無い・JSON でない・辞書でない・形の番号が違う、のどれかなら BackupError。"""
    try:
        data = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError(f"控えのフォルダではありません (backup.json が無いか、形が違います): {folder}")
    return data


def _size(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def list_backups(parent: Path) -> list[dict]:
    """置き場の下の控えを名前の順に。名前の形が合い、backup.json を読めるフォルダだけ。置き場が無ければ []。"""
    parent = Path(parent)
    if not parent.is_dir():
        return []
    rows = []
    for folder in sorted(parent.iterdir(), key=lambda path: path.name):
        if not folder.is_dir() or NAME_PATTERN.match(folder.name) is None:
            continue
        try:
            manifest = read_manifest(folder)
        except BackupError:
            continue
        rows.append(
            {
                "name": folder.name,
                "created_at": str(manifest.get("created_at", "")),
                "database": str(manifest.get("database", "")),
                "seizou_version": str(manifest.get("seizou_version", "")),
                "size": _size(folder),
            }
        )
    return rows


def name_time(name: str) -> datetime | None:
    """控えのフォルダの名前の頭の日時 (置き場の時計の現地時刻。tz 無し)。形が合わなければ None。"""
    if NAME_PATTERN.match(name) is None:
        return None
    try:
        return datetime.strptime(name[:NAME_TIME_LENGTH], NAME_TIME_FORMAT)
    except ValueError:
        return None


def newest_backup(parent: Path) -> dict | None:
    """置き場の一番新しい控え (list_backups の行)。戻す前の退避 (-before-restore) は飛ばす。無ければ None。"""
    rows = [
        row
        for row in list_backups(parent)
        if not row["name"].endswith(RETREAT_SUFFIX) and name_time(row["name"]) is not None
    ]
    return max(rows, key=lambda row: name_time(row["name"])) if rows else None


def prune_backups(parent: Path, keep_days: int, *, now: datetime | None = None, keep=()) -> list[str]:
    """名前の日時が now - keep_days 日より古い控えのフォルダを消し、消した名前を名前の順に返す。

    消さない物: 戻す前の退避 (-before-restore)・keep に渡した名前 (今取った控え)・名前の形が合わない物・
    backup.json を読めない物 (控えでない物には触らない)。日時はフォルダの名前で決め、mtime は見ない (写し直しで動くため)。
    now は置き場の時計の現地時刻 (tz 無し。new_name と同じ時計)。既定は今。
    訳: 古い控えを手の find で消さずに済ませ、世代を日数で保つため。退避を消さないのは、戻しを取り消す道を残すため。出所 = エンタープライズ化の調べを受けた作者の決めと開発の記録 docs/nights/20261108/N358.md。変えてよい条件 = 本人が世代の数え方 (日数・回数) を決め直したとき。
    """
    parent = Path(parent)
    if not parent.is_dir():
        return []
    cutoff = (now or datetime.now()) - timedelta(days=keep_days)
    removed = []
    for folder in sorted(parent.iterdir(), key=lambda path: path.name):
        name = folder.name
        if not folder.is_dir() or folder.is_symlink() or name.endswith(RETREAT_SUFFIX) or name in keep:
            continue
        taken = name_time(name)
        if taken is None or taken >= cutoff:
            continue
        try:
            read_manifest(folder)
        except BackupError:
            continue
        shutil.rmtree(folder)
        removed.append(name)
    return removed


def resolve_folder(arg: str, parent: Path) -> Path:
    """控えのフォルダ。arg がフォルダの道ならそれ、無ければ置き場の中の名前として探す。"""
    path = Path(arg)
    if path.is_dir():
        return path
    candidate = Path(parent) / arg
    if candidate.is_dir():
        return candidate
    raise BackupError(f"控えが見つかりません: {arg} (一覧は seizou_restore --list)")


@contextmanager
def _readonly_copy(db_file: Path):
    """控えの DB の写しを一時のフォルダに写し、標準の sqlite3 の読むだけの接続で開く (閉じたら一時のフォルダごと消える)。

    WAL の形の写しは、読むだけで開いても隣に -shm と -wal を作るので、控えのフォルダでは開かない (控えの中身を変えない)。
    """
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / db_file.name
        shutil.copyfile(db_file, copy)
        conn = sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()


def _quick_check(conn) -> list[str]:
    """PRAGMA quick_check の答え。ok なら []、そうでなければ最初の数行。開けない写しは sqlite3.DatabaseError。"""
    answers = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
    if answers == ["ok"]:
        return []
    return answers[:_QUICK_CHECK_LINES] or ["答えが無い"]


def _saved_migrations(conn) -> set[tuple[str, str]] | None:
    """控えの写しの django_migrations の (app, name)。表が無い (migrate の前の控えか、中身の無い控え) なら None。"""
    try:
        rows = conn.execute("SELECT app, name FROM django_migrations").fetchall()
    except sqlite3.OperationalError:
        return None
    return {(str(app), str(name)) for app, name in rows}


def _migration_line(saved: set[tuple[str, str]] | None) -> str:
    """控えと今の DB の当てた migration の差の 1 行 (版の違いの目安。止めない)。今の DB は ORM で読むだけ。"""
    if saved is None:
        return "migration: 控えに django_migrations の表がありません (migrate の前の控えか、中身の無い控え)"
    try:
        current = set(MigrationRecorder(connection).applied_migrations())
    except DatabaseError:
        return "migration: 今のデータベースを読めないので、控えと比べていません"
    only_saved = len(saved - current)
    only_now = len(current - saved)
    if not (only_saved or only_now):
        return f"migration: 控えと今で同じ ({len(saved)} 件)"
    return f"migration: 控えにだけある {only_saved} 件・今にだけある {only_now} 件 (版の違いの目安。戻すときは止めない)"


def verify_backup(folder: Path) -> dict:
    """控えから戻せるかを、本番の DB と上げたファイルに触らずに確かめる (seizou_restore --verify の中身)。

    見る物は、控えの DB の写しが開けるか (PRAGMA quick_check)・控えと今の DB の当てた migration の差・
    控えの media/ のファイルの数と backup.json の media_files・backup.json の sha256 の有無。
    成否を偽にするのは、DB の写しが無いか開けないか quick_check が ok でないときと、上げたファイルの数が違うとき。
    migration の差と sha256 の無いことと seizou の版の違いは行に書くだけで、成否を変えない。
    表ごとの行の数は比べない。本番の DB に書かず、MEDIA_ROOT に触らず、控えのフォルダの中身を変えない。
    返す物は {"ok": 成否, "lines": 行の並び (字), "seconds": かかった秒}。
    控えのフォルダでない・SQLite でない (控えか今の DB) は BackupError (確かめられない)。

    訳: 控えから本当に戻せるかを、本番の DB と上げたファイルに触らずに確かめ、確かめた日を残すため (戻しの時間と失ってよい幅を測る材料)。本番の process の中で DB を切り替えない決め (practice.py の docstring) を守るため、控えの写しは標準の sqlite3 の読むだけの接続で quick_check と django_migrations の読みにとどめる (SQL の字はこの 2 つだけ。R14 の訳)。出所 = エンタープライズ化の調べを受けた作者の決め。変えてよい条件 = 控えに表ごとの行の数か中身の印 (作者の決め) を持たせたとき、比べる物を足す。
    """
    started = time.monotonic()
    folder = Path(folder)
    manifest = read_manifest(folder)
    saved_kind = str(manifest.get("database") or "")
    kind = database_kind()
    if saved_kind != "sqlite" or kind != "sqlite":
        raise BackupError(VERIFY_SQLITE_ONLY.format(saved=saved_kind, kind=kind))
    ok = True
    lines = []
    db_file = folder / Path(str(manifest.get("db_file") or "")).name
    if not db_file.is_file():
        ok = False
        lines.append(f"DB の写し: 控えにありません ({db_file.name})")
    else:
        saved = None
        try:
            with _readonly_copy(db_file) as conn:
                problems = _quick_check(conn)
                if not problems:
                    saved = _saved_migrations(conn)
        except sqlite3.DatabaseError as exc:
            problems = [f"開けません。{exc}"]
        except OSError as exc:
            raise BackupError(f"控えの DB の写しを読めません: {db_file} ({exc.strerror})。何も変えていません。") from exc
        if problems:
            ok = False
            lines.append(f"DB の写し ({db_file.name}): 壊れています (quick_check: {'・'.join(problems)})")
        else:
            lines.append(f"DB の写し ({db_file.name}): 開けます (quick_check: ok)")
            lines.append(_migration_line(saved))
    saved_files = manifest.get("media_files")
    files = _count_files(folder / MEDIA_DIR)
    if files == saved_files:
        lines.append(f"上げたファイル: {files} 個 (backup.json と同じ)")
    else:
        ok = False
        lines.append(f"上げたファイルの数が違います: 控えの media/ に {files} 個、backup.json は {saved_files} 個")
    if "sha256" not in manifest:
        lines.append("sha256 無し (中身の印を比べていない)")
    saved_version = str(manifest.get("seizou_version") or "")
    version = current_version()
    if saved_version != version:
        lines.append(f"控えの seizou の版 ({saved_version}) と今の版 ({version}) が違います。")
    return {"ok": ok, "lines": lines, "seconds": round(time.monotonic() - started, 2)}


def record_restore_check(parent: Path, folder: Path, result: dict) -> Path:
    """確かめの結果を、控えの置き場の restore-checks.jsonl に 1 行足し、記録のファイルの道を返す。

    書くのは日時 (置き場の時計の現地時刻)・控えの名前・かかった秒・成否だけ (鍵の値と DB の中身は書かない)。
    """
    path = Path(parent) / RESTORE_CHECKS
    row = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "backup": Path(folder).name,
        "seconds": result["seconds"],
        "ok": bool(result["ok"]),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise BackupError(f"確かめの記録を書けません: {path} ({exc.strerror})") from exc
    return path


def last_restore_check(parent: Path) -> datetime | None:
    """restore-checks.jsonl の成功の行のうち、一番新しい日時 (置き場の時計の現地時刻)。

    ファイルが無いか読めなければ None。JSON でない行・成功でない行・日時の読めない行は飛ばす (例外を外に出さない)。
    """
    try:
        text = (Path(parent) / RESTORE_CHECKS).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    latest = None
    for line in text.splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("ok") is not True:
                continue
            when = datetime.fromisoformat(str(row.get("checked_at")))
        except ValueError:
            continue
        if when.tzinfo is not None:
            when = when.astimezone().replace(tzinfo=None)
        if latest is None or when > latest:
            latest = when
    return latest


def _refuse_broken_db(db_file: Path) -> None:
    """戻しの頭 (退避を作る前) で呼ぶ。控えの DB の写しを --verify と同じ quick_check で確かめ、壊れていれば BackupError で止める。

    止めるのは quick_check が ok でないときと、写しが開けない (sqlite3.DatabaseError) ときだけ。migration の差では止めない
    (版の違いは戻した後の migrate で埋める)。止めるときは退避を作らず、本番の DB と上げたファイルに触らない。
    訳: SQLite の backup API は控えの中身を確かめずに page を写すので、壊れた控えで本番の DB を上書きしないため。出所 = エンタープライズ化の調べを受けた作者の決め。変えてよい条件 = 控えの確かめ (控えの sha256・integrity_check) が入ったら、その確かめに寄せる。
    """
    try:
        with _readonly_copy(db_file) as conn:
            problems = _quick_check(conn)
    except sqlite3.DatabaseError as exc:
        problems = [f"開けません。{exc}"]
    except OSError as exc:
        raise BackupError(f"控えの DB の写しを読めません: {db_file} ({exc.strerror})。何も変えていません。") from exc
    if problems:
        raise BackupError(
            f"控えの DB が壊れています (quick_check: {'・'.join(problems)})。別の控えを選んでください。何も変えていません。"
        )


def restore_backup(folder: Path, *, retreat_parent: Path) -> dict:
    """控えから DB と上げたファイルを戻す。戻す前に今の物を retreat_parent に控える (退避が失敗したら戻さない)。

    退避の前に、控えの DB を --verify と同じ quick_check で確かめ、壊れていれば何も変えずに止める (_refuse_broken_db)。
    answers.yaml は戻さない (置き場の settings.py と対の物で、データではない)。
    site/ (会社の作り込み。命令 seizou_backup の控えだけ) は控えに入るが、この関数は戻さない (置き場のコードを控えの古い物で上書きしないため。置き場ごと失ったときは手引きのとおり手で写す)。
    上げたファイルの入れ替えが途中で止まったら、退避の在りかと打ち直しの案内を BackupError で返す。
    返す物は {"folder": 控え, "retreat": 退避のフォルダ, "manifest": 控えの backup.json}。
    """
    _refuse_bad_media_root()
    folder = Path(folder)
    manifest = read_manifest(folder)
    kind = database_kind()
    if manifest.get("database") != kind:
        raise BackupError(f"控えの DB ({manifest.get('database')}) と今の DB ({kind}) が合いません。")
    saved_company = str(manifest.get("company_id") or "")
    company = conf.company_id()
    if saved_company and company and saved_company != company:
        raise BackupError(
            f"控えの会社の id ({saved_company}) と今の会社の id ({company}) が違います (別の会社の控え)。"
        )
    if kind == "postgresql":
        name = str(database_settings().get("NAME", ""))
        db_path = folder / DB_FILES[kind]
        raise BackupError(
            PG_RESTORE_GUIDE.format(name=name, path=db_path, media=folder / MEDIA_DIR, media_root=_media_root() or "MEDIA_ROOT")
        )
    if connection.in_atomic_block:
        raise BackupError("transaction の中では戻せません。")
    db_file = folder / Path(str(manifest.get("db_file") or "")).name
    if not db_file.is_file():
        raise BackupError(f"控えに DB の写しがありません: {db_file}")
    _refuse_broken_db(db_file)

    retreat = take_backup(retreat_parent, suffix=RETREAT_SUFFIX)

    try:
        source = sqlite3.connect(str(db_file))
        try:
            connection.ensure_connection()
            source.backup(connection.connection)
        finally:
            source.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError(
            f"控えの DB を写せませんでした ({exc})。上げたファイルはまだ変えていません。"
            f"戻す前の物は {retreat['folder']} にあります。"
        ) from exc

    root = _media_root()
    if root is not None:
        try:
            if root.exists():
                shutil.rmtree(root)
            saved_media = folder / MEDIA_DIR
            if saved_media.is_dir():
                shutil.copytree(saved_media, root)
            else:
                # 控えのときに上げたファイルが無かった = 無い状態に戻す (DB の行と食い違わせない。退避にある)
                root.mkdir(parents=True)
        except OSError as exc:
            # copytree の shutil.Error は strerror を持たないので、そのときは理由を添えない
            reason = f" ({exc.strerror})" if exc.strerror else ""
            raise BackupError(
                f"データベースは戻しましたが、上げたファイル ({root}) の入れ替えが途中で止まりました{reason}。"
                f"ほかの道具で開いているファイルを閉じてから、もう 1 度 seizou_restore {folder} を打つと、控えのとおりに戻ります。"
                f"戻す前の物は {retreat['folder']} にあります。"
            ) from exc

    conf.reset_cache()
    return {"folder": folder, "retreat": retreat["folder"], "manifest": manifest}
