"""命令 seizou (Claude Code 無しで動く)。

seizou new <answers.yaml> [--demo]
    答えの YAML から会社のプロジェクト (~/seizou-sites/<id>/) を作り、migrate し、管理者を 1 人作る。
    item_fields があれば会社ごとの項目の app (fields/) も作る。--demo は作り物のデータを入れる口。
    置き場の親は環境変数 SEIZOU_SITES_DIR で変えられる (<その値>/<id>/)。
    起動はしない。起動の 1 行を最後に出す。

終わりの番号: 0 = できた / 1 = migrate などの子の process が失敗した / 2 = 答えか置き場の誤り。

Django はこの module の中で import しない。Django を使う仕事は、作った manage.py を子の process で回す。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import secrets
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path
from string import Template

import yaml

EXIT_OK = 0
EXIT_CHILD = 1
EXIT_INPUT = 2

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
FIELD_TYPES = ("text", "number", "date", "choice", "bool")
FIELD_FLAGS = ("required", "in_list", "searchable")
REQUIRED_KEYS = ("id", "name", "port")

# seizou.core.conf.DEFAULTS と同じ値 (core は Django を読むので、ここでは import せずに写す)。
SETTING_DEFAULTS = {
    "order_form": "multi",
    "partial_shipment": True,
    "over_shipment": "warn",
    "over_workorder": "warn",
    "order_number": "seq",
    "hidden_fields": [],
    "production_plan": True,
    "access_control": {"department": "log", "write": "log", "money": "log"},
    "assistant_model": "claude-sonnet-5-5",
    "intake_model": "claude-opus-5-5",
    "menu": [],
}

ADMIN_ID = "admin"

# 管理者を作る短い Python (manage.py shell -c に渡し、子の process の中だけで Django を読む)。
# ID とパスワードは環境変数で受ける。import の文を書かず importlib で読むのは、この module に
# Django の import の文が無いことを grep で確かめられるようにするため。
CREATE_ADMIN = (
    "import importlib, os; "
    "User = importlib.import_module('django.contrib.auth').get_user_model(); "
    "uid = os.environ['SEIZOU_ADMIN_ID']; "
    "made = not User.objects.filter(username=uid).exists(); "
    "made and User.objects.create_superuser(uid, password=os.environ['SEIZOU_ADMIN_PASSWORD']); "
    "print('created' if made else 'exists')"
)

# 会社の設定 (core の Company) の 1 行を、答えの会社名で作る短い Python。既にあれば触らない。
CREATE_COMPANY = (
    "import importlib, os; "
    "Company = importlib.import_module('seizou.core.models').Company; "
    "made = not Company.objects.exists(); "
    "made and Company.objects.create(name=os.environ['SEIZOU_COMPANY_NAME']); "
    "print('created' if made else 'exists')"
)

# 作り物のデータを入れる短い Python (seizou.core.demo があるときだけ。引数は種のフォルダ)。
LOAD_DEMO = "import importlib, pathlib; importlib.import_module('seizou.core.demo').load(pathlib.Path({seeds!r}))"


class AnswerError(Exception):
    """答えか置き場の誤り (終わりの番号 2)。文は日本語。"""


def sites_dir() -> Path:
    value = os.environ.get("SEIZOU_SITES_DIR")
    if value:
        return Path(value)
    return Path.home() / "seizou-sites"


def load_answers(path: Path) -> dict:
    """答えの YAML を読み、鍵と形を確かめる。誤りは AnswerError。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AnswerError(f"答えの YAML を開けません: {path} ({exc.strerror})") from exc
    try:
        answers = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AnswerError(f"答えの YAML を読めません: {path}") from exc
    if not isinstance(answers, dict):
        raise AnswerError(f"答えの YAML の形が違います (鍵と値の並びではありません): {path}")

    missing = [key for key in REQUIRED_KEYS if answers.get(key) in (None, "")]
    if missing:
        raise AnswerError(f"答えの YAML に {'・'.join(missing)} がありません: {path}")

    company_id = answers["id"]
    if not isinstance(company_id, str) or not ID_PATTERN.match(company_id):
        raise AnswerError(
            f"会社の id の形が違います: {company_id!r}。英小文字で始め、英小文字と数字と _ だけで書きます (ハイフンと大文字は使えません)。"
        )

    port = answers["port"]
    if isinstance(port, bool) or not isinstance(port, int):
        raise AnswerError(f"port は整数で書きます: {port!r}")

    item_fields = answers.get("item_fields") or []
    if not isinstance(item_fields, list):
        raise AnswerError("item_fields は項目の並びで書きます。")
    for number, field in enumerate(item_fields, start=1):
        if not isinstance(field, dict):
            raise AnswerError(f"item_fields の {number} 番目の形が違います。")
        if field.get("type") not in FIELD_TYPES:
            raise AnswerError(
                f"item_fields の {number} 番目の型を知りません: {field.get('type')!r}。"
                f"{' / '.join(FIELD_TYPES)} のどれかで書きます。"
            )
    return answers


def settings_with_defaults(answers: dict) -> tuple[dict, list[str]]:
    """答えの settings に、無い鍵を既定で補った辞書と、既定を使った鍵の一覧。"""
    given = answers.get("settings") or {}
    if not isinstance(given, dict):
        raise AnswerError("settings は鍵と値の並びで書きます。")
    values = dict(given)
    used_defaults = []
    for key, default in SETTING_DEFAULTS.items():
        if key not in values:
            values[key] = default
            used_defaults.append(key)
    return values, used_defaults


def item_columns(answers: dict) -> tuple[list[dict], list[str]]:
    """item_fields に列の名前 (ext01…) を付けた並びと、既定 (偽) を使った所の一覧。"""
    columns = []
    used_defaults = []
    for number, field in enumerate(answers.get("item_fields") or [], start=1):
        column = {"name": f"ext{number:02d}", "label": field.get("label"), "type": field["type"]}
        for flag in FIELD_FLAGS:
            if flag in field:
                column[flag] = bool(field[flag])
            else:
                column[flag] = False
                used_defaults.append(f"item_fields の {number} 番目の {flag} = False")
        column["choices"] = [str(choice) for choice in field.get("choices") or []]
        columns.append(column)
    return columns, used_defaults


def model_line(column: dict) -> str:
    """拡張の表の 1 列。文字の上限は 100 (01 の 4 節)。必須でなければ空を許す。"""
    label = repr(column["label"])
    optional = not column["required"]
    blank = ", blank=True" if optional else ""
    null_blank = ", null=True, blank=True" if optional else ""
    kind = column["type"]
    if kind == "text":
        field = f"models.CharField({label}, max_length=100{blank})"
    elif kind == "choice":
        choices = [(choice, choice) for choice in column["choices"]]
        field = f"models.CharField({label}, max_length=100, choices={choices!r}{blank})"
    elif kind == "number":
        field = f"models.DecimalField({label}, max_digits=14, decimal_places=2{null_blank})"
    elif kind == "date":
        field = f"models.DateField({label}{null_blank})"
    else:
        field = f"models.BooleanField({label}, default=False{blank})"
    return f"    {column['name']} = {field}"


def fields_installed() -> bool:
    """会社の settings.py と同じ判定 (部品マスターが PARTS にあるときだけ fields を入れる)。"""
    from seizou.parts import PARTS

    return "seizou.masters" in PARTS


def write_fields(site: Path, company_id: str, columns: list[dict]) -> None:
    folder = site / "fields"
    (folder / "migrations").mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "migrations" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "apps.py").write_text(
        render(
            "fields_apps.py.tmpl",
            id=company_id,
            item_fields="\n".join(f"    {column!r}," for column in columns),
        ),
        encoding="utf-8",
    )
    (folder / "models.py").write_text(
        render("fields_models.py.tmpl", id=company_id, columns="\n".join(model_line(c) for c in columns)),
        encoding="utf-8",
    )


def render(template_name: str, **values) -> str:
    text = resources.files("seizou.scaffold").joinpath(template_name).read_text(encoding="utf-8")
    return Template(text).substitute(values)


def seizou_items(answers: dict, settings: dict) -> str:
    """settings.py の SEIZOU の辞書の中身。鍵ごとに 1 行、値は Python の literal。"""
    values = {"id": answers["id"], **settings, "port": answers["port"]}
    return "\n".join(f"    {key!r}: {value!r}," for key, value in values.items())


def start_line(site: Path, port: int) -> str:
    python = sys.executable
    if " " in python:
        python = f'"{python}"'
    place = str(site)
    if " " in place:
        place = f'"{place}"'
    return f"cd {place} && {python} manage.py runserver {port}"


def write_secret_file(path: Path, text: str) -> None:
    """持ち主だけが読める形で書く (Windows では権限を無視する)。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def write_project(site: Path, answers_path: Path, answers: dict, settings: dict) -> None:
    company_id = answers["id"]
    site.mkdir(parents=True, exist_ok=True)

    target = site / "answers.yaml"
    if not (target.exists() and os.path.samefile(answers_path, target)):
        shutil.copyfile(answers_path, target)

    (site / "manage.py").write_text(render("manage.py.tmpl", id=company_id), encoding="utf-8")
    (site / "settings.py").write_text(
        render("settings.py.tmpl", id=company_id, seizou_items=seizou_items(answers, settings)),
        encoding="utf-8",
    )
    write_secret_file(site / ".env", render("env.tmpl", secret_key=secrets.token_urlsafe(50)))
    (site / "README.md").write_text(
        render(
            "README.md.tmpl",
            id=company_id,
            name=str(answers["name"]),
            port=answers["port"],
            start_line=start_line(site, answers["port"]),
            admin_id=ADMIN_ID,
        ),
        encoding="utf-8",
    )
    (site / "checks.py").write_text(render("checks.py.tmpl", id=company_id), encoding="utf-8")
    (site / "deploy").mkdir(parents=True, exist_ok=True)
    (site / "deploy" / "nginx-files.conf").write_text(
        render("nginx-files.conf.tmpl", id=company_id, media_root=(site.resolve() / "media").as_posix()),
        encoding="utf-8",
    )
    for folder in ("templates", "static/css", "manual/images"):
        (site / folder).mkdir(parents=True, exist_ok=True)


def child_env(extra: dict | None = None) -> dict:
    # 代入にする。親 (pytest-django など) の DJANGO_SETTINGS_MODULE を子に引き継がない。
    return {**os.environ, "DJANGO_SETTINGS_MODULE": "settings", **(extra or {})}


def run_manage(site: Path, args: list[str], extra_env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=site,
        env=child_env(extra_env),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def show_failure(step: str, result: subprocess.CompletedProcess) -> int:
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    print(f"止まりました: {step} が失敗しました (終わりの番号 {result.returncode})。上の出力を見てください。", file=sys.stderr)
    return EXIT_CHILD


def command_new(answers_path: Path, demo: bool) -> int:
    print("答えの YAML から会社のプロジェクトを作ります。")
    print("置き場にファイルを書き、データベースを用意し (migrate)、管理者を 1 人作ります。起動はしません。")

    try:
        answers = load_answers(answers_path)
        settings, used_defaults = settings_with_defaults(answers)
        columns, field_defaults = item_columns(answers)
    except AnswerError as exc:
        print(f"止まりました: {exc}", file=sys.stderr)
        return EXIT_INPUT

    site = sites_dir() / answers["id"]
    existing = [name for name in ("manage.py", "settings.py") if (site / name).exists()]
    if existing:
        print(
            f"止まりました: 置き場 {site} に {'・'.join(existing)} が既にあります。上書きしません。"
            "作り直すなら、置き場を消してからもう 1 度打ってください。",
            file=sys.stderr,
        )
        return EXIT_INPUT

    if used_defaults:
        shown = "、".join(f"{key} = {SETTING_DEFAULTS[key]!r}" for key in used_defaults)
        print(f"答えに無い設定は既定を使いました: {shown}")
    if field_defaults:
        print(f"答えに無い項目の属性は既定を使いました: {'、'.join(field_defaults)}")

    answers_path = answers_path.resolve()
    write_project(site, answers_path, answers, settings)
    print(f"ファイルを書きました: {site}")

    if columns:
        write_fields(site, answers["id"], columns)
        if fields_installed():
            result = run_manage(site, ["makemigrations", "fields"])
            if result.returncode != 0:
                return show_failure("会社ごとの項目の migration を作る所 (makemigrations fields)", result)
            print("会社ごとの項目の表 (fields/) と、その migration を作りました。")
        else:
            print("会社ごとの項目の表 (fields/) を作りました。部品マスター (seizou.masters) が入るまでは使いません。")

    result = run_manage(site, ["migrate", "--no-input"])
    if result.returncode != 0:
        return show_failure("migrate", result)
    print("データベースを用意しました (migrate)。")

    password = secrets.token_urlsafe(12)
    result = run_manage(
        site,
        ["shell", "--no-imports", "-c", CREATE_ADMIN],
        {"SEIZOU_ADMIN_ID": ADMIN_ID, "SEIZOU_ADMIN_PASSWORD": password},
    )
    if result.returncode != 0:
        return show_failure("管理者を作る所", result)
    if result.stdout.strip().splitlines()[-1:] == ["exists"]:
        admin_text = f"管理者の ID: {ADMIN_ID} (既にあったので作らず、パスワードも変えていません)"
    else:
        admin_text = f"管理者の ID: {ADMIN_ID} / パスワード: {password} (パスワードはこの 1 度だけ出します)"

    # 会社の設定の 1 行 (画面の上の帯に出る会社名)。答えの name を使う。
    result = run_manage(
        site,
        ["shell", "--no-imports", "-c", CREATE_COMPANY],
        {"SEIZOU_COMPANY_NAME": str(answers["name"])},
    )
    if result.returncode != 0:
        return show_failure("会社の設定を作る所", result)
    print(f"会社の設定を作りました: {answers['name']}")

    if demo:
        # import せずに有無だけを見る (この process で Django の model を読まないため)。
        if importlib.util.find_spec("seizou.core.demo") is None:
            print("作り物のデータの読み込みは後の版です (seizou.core.demo が無い)。")
        else:
            # 種は答えの YAML と同じ場所の <id>/ (examples/companies/README.md の約束)。
            seeds = answers_path.parent / answers["id"]
            result = run_manage(site, ["shell", "--no-imports", "-c", LOAD_DEMO.format(seeds=str(seeds))])
            if result.returncode != 0:
                return show_failure("作り物のデータの読み込み", result)
            # 読み込みの出力 (種類ごとの「入れた n 件・飛ばした m 件」と「注意」の行) をそのまま画面に出す
            sys.stdout.write(result.stdout or "")
            print(f"作り物のデータを登録しました: {seeds}")

    port = answers["port"]
    print(f"できた物: {site} の会社のプロジェクト。{admin_text}")
    print(f"次にすること: {start_line(site, port)} で起動し、http://127.0.0.1:{port}/login/ を開いてログインします。")
    print("練習するときは: 置き場で manage.py seizou_practice start (今のデータの写しを使うサーバーを別のポートで立てる。ここでの登録は本番に残らない。閉じるときは seizou_practice stop)。")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seizou", description="町工場の会社のシステムを作る命令。")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser(
        "new",
        help="答えの YAML から会社のプロジェクトを作る",
        description="答えの YAML から会社のプロジェクト (~/seizou-sites/<id>/) を作り、migrate し、管理者を 1 人作る。",
    )
    new.add_argument("answers", type=Path, help="答えの YAML のパス")
    new.add_argument("--demo", action="store_true", help="作り物のデータも登録する (作り物の会社のとき)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "new":
        return command_new(args.answers, args.demo)
    return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
