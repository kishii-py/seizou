"""控え (バックアップ) と戻し (復元)。命令 seizou_backup・seizou_restore の中身。

控えは日時の名前のフォルダ 1 つ = DB の写し + media/ + answers.yaml + backup.json。.env (鍵) は控えない。
命令 seizou_backup の控えだけは、写した DB を integrity_check で確かめ、会社の作り込みの許しの一覧を site/ に写し、各ファイルの sha256 と当てた migration を backup.json に足す (戻す前の退避と練習モードの写しは遅くしない)。
DB の写しは SQLite の backup API (Django の生きている接続から。WAL でも整合が取れる)。
PostgreSQL は pg_dump を呼ぶ (無ければ案内して止まる)。
戻す前に同じ置き場に今の物を控える (戻しの戻し)。

命令は引数を読んで結果を書くだけで、処理はこの module に置く。表に書くのは戻しの backup API だけで、
ORM で表に書く処理は無い。部品 (connect など) を import しない。
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
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import connection
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
SITE_DIR = "site"
SITE_FILES = ("settings.py", "manage.py", "CLAUDE.md", "AGENTS.md", "README.md")
SITE_DIRS = ("fields", "templates", "static", "manual", "deploy")
#: 作り込みのフォルダを写すときに外す物
SITE_IGNORE = ("__pycache__",)
#: integrity_check が通らないときに断りの文に添える行の数
INTEGRITY_LINES = 3

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
    会社の作り込みの許しの一覧 (SITE_FILES・SITE_DIRS) を site/ に写し、backup.json に sha256・migrations・site_files の鍵を足す。
    戻す前の退避と練習モードの写しは full を渡さない (今のまま)。
    PostgreSQL の写し (db.pgdump) は確かめない。
    訳: 壊れた控えを黙って残さず、戻した時に会社ごとの項目や帳票の形も一緒に戻せるようにするため。作り込みを入れるのは本人の決め (11-08 00:0x)。確かめと写しを命令の道だけにしたのは、生きた DB が壊れたときに戻す前の退避と練習モードが止まらないように (確かめ直しの (b))。.env は控えない決まり (backup.py の docstring・docs/operations.md の「毎日の控え」) のまま。出所 = エンタープライズ化の調べ 第一弾の指摘 ops-3・ops-4・chk-ops-sre-5 と確かめ直しの (b)(f)。変えてよい条件 = 本人が控えの範囲を決め直したとき、または戻す前の退避にも確かめを掛けると決めたとき。
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

    訳: 壊れた写しを控えとして黙って残さないため。出所 = エンタープライズ化の調べ 第一弾の指摘 ops-3 と確かめ直しの (b)(g)。変えてよい条件 = 戻す前の退避にも確かめを掛けると本人が決めたとき (呼び手を増やす)。
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


def resolve_folder(arg: str, parent: Path) -> Path:
    """控えのフォルダ。arg がフォルダの道ならそれ、無ければ置き場の中の名前として探す。"""
    path = Path(arg)
    if path.is_dir():
        return path
    candidate = Path(parent) / arg
    if candidate.is_dir():
        return candidate
    raise BackupError(f"控えが見つかりません: {arg} (一覧は seizou_restore --list)")


def restore_backup(folder: Path, *, retreat_parent: Path) -> dict:
    """控えから DB と上げたファイルを戻す。戻す前に今の物を retreat_parent に控える (退避が失敗したら戻さない)。

    answers.yaml は戻さない (置き場の settings.py と対の物で、データではない)。
    site/ (会社の作り込み。命令 seizou_backup の控えだけ) は控えに入るが、この関数は戻さない (置き場のコードを控えの古い物で上書きしないため。置き場ごと失ったときは手引きのとおり手で写す)。
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

    retreat = take_backup(retreat_parent, suffix=RETREAT_SUFFIX)

    source = sqlite3.connect(str(db_file))
    try:
        connection.ensure_connection()
        source.backup(connection.connection)
    finally:
        source.close()

    root = _media_root()
    if root is not None:
        if root.exists():
            shutil.rmtree(root)
        saved_media = folder / MEDIA_DIR
        if saved_media.is_dir():
            shutil.copytree(saved_media, root)
        else:
            # 控えのときに上げたファイルが無かった = 無い状態に戻す (DB の行と食い違わせない。退避にある)
            root.mkdir(parents=True)

    conf.reset_cache()
    return {"folder": folder, "retreat": retreat["folder"], "manifest": manifest}
