"""命令 seizou (Claude Code 無しで動く)。

seizou new <answers.yaml> [--demo] [--password-file <path>]
    答えの YAML から会社のプロジェクト (~/seizou-sites/<id>/) を作り、migrate し、管理者を 1 人作る。
    item_fields があれば会社ごとの項目の app (fields/) も作る。--demo は作り物のデータを入れる口。
    --demo のつながりの種の受け口の URL の頭 (http://127.0.0.1:800x) は、答えの partner_base_urls (相手の会社の id → 置き場の URL の頭)
    か環境変数 SEIZOU_PARTNER_<相手の id の大文字>_URL があれば、その頭に置き換えて入れる (環境変数が勝つ。外の置き場の https のため)。
    管理者の ID とパスワードは「できた物」の後に 1 行ずつ出す。--password-file を渡すと、パスワードを画面に出さず、
    そのファイルに admin:<値> の 1 行で書く (持ち主だけが読める)。
    画面ごとの説明書の断片は、部品の中の標準の組 (core/manual_default/) を置き場の manual/ に写す。
    --demo のときは種の断片を先に写すので、同じ名前は種の断片が勝つ (上書きしない)。
    置き場には、AI が守る決まりの CLAUDE.md (Claude Code が置き場を開いたときに読む) と、その短い版の AGENTS.md も書く。
    置き場には、git に入れない物 (鍵の .env・DB・上げたファイルの media/ ほか) を並べた .gitignore も書く (既にあれば書き直さない)。
    置き場には、Claude Code に .env・DB・控え (backups/) を読ませない .claude/settings.json も書く (既にあれば書き直さない)。
    置き場は、プラグインの MCP が起こしてよい置き場の一覧 (~/.seizou/sites.json。seizou.site_registry) に、この python で登録する。
    --demo-password random は、作り物の利用者 3 人 (genba・jimu・keiri) に別々の乱数のパスワードを作り、1 人 1 行で出す
    (<値> なら 3 人ともその値)。既定は docs/getting-started.md に書いた公開の合言葉 (手元で試す置き場のため)。
    答えの industry が業種の見本 (src/seizou/recipes/<業種の id>.yaml) に当たると、答えに無い項目と設定を見本から補い、
    工程を見本から入れる (答えが勝つ。recipe: none なら入れない)。
    置き場の親は環境変数 SEIZOU_SITES_DIR で変えられる (<その値>/<id>/)。
    起動はしない。起動の 1 行を最後に出す。起動の 1 行は、Windows では PowerShell で打てる形 (cd "<置き場>"; & "<python>" manage.py runserver <port>) にする。
seizou ai-docs <置き場> [--force]
    既にある会社の置き場 (manage.py と answers.yaml があるフォルダ) に、seizou new と同じ CLAUDE.md と AGENTS.md を後から書く。どちらかが既にあれば、--force のときだけ 2 つとも書き直す。
    .gitignore が無ければ同じく書く (既にある .gitignore は --force でも書き直さない)。
    .claude/settings.json が無ければ、CLAUDE.md と AGENTS.md が既にあって止まる前に書く (既にある物は --force でも書き直さない)。
    置き場を、プラグインの MCP が起こしてよい置き場の一覧に、この python で登録する (これも止まる前。版を上げた後に打つ)。
seizou github-token [<置き場>]
    会社の置き場 (既定は今のフォルダ) の .env に、フィードバックを GitHub に送るトークンと送り先と口座の名前の 3 行を書く (開発の記録 docs/nights/20261106/N185.md)。
    トークンは画面に出さずに聞き、GitHub に口座の名前を聞いて確かめてから書く。出すのは口座の名前だけ。
    画面の process が置き場の .env に書けない置き場のための道 (ふだんは画面「GitHub とつなぐ」で貼る)。

終わりの番号: 0 = できた / 1 = migrate などの子の process が失敗した / 2 = 答えか置き場の誤り。

Django はこの module の中で import しない。Django を使う仕事は、作った manage.py を子の process で回す。
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import unicodedata
from importlib import resources
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

import yaml

from seizou import site_registry

EXIT_OK = 0
EXIT_CHILD = 1
EXIT_INPUT = 2

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
FIELD_TYPES = ("text", "number", "date", "choice", "bool")
FIELD_FLAGS = ("required", "in_list", "searchable")
REQUIRED_KEYS = ("id", "name", "port")
#: 答えの YAML の company の下に書ける鍵 (core.services.COMPANY_INFO_FIELDS のうち字の物。ロゴは根の鍵 logo か設定の画面から上げる)
COMPANY_KEYS = ("address", "phone", "bank_account", "registration_no")
#: core.services.REGISTRATION_NO_PATTERN と同じ形 (core は Django を読むので import せずに写す。作者の決め)
REGISTRATION_NO = re.compile(r"T[0-9]{13}")
#: --demo のつながりの種の受け口の頭を、相手の置き場の URL にする環境変数 (SEIZOU_PARTNER_<相手の会社の id の大文字>_URL)。答えの partner_base_urls より勝つ
PARTNER_URL_ENV = re.compile(r"SEIZOU_PARTNER_([A-Z][A-Z0-9_]*)_URL")

# seizou.core.conf.DEFAULTS と同じ値 (core は Django を読むので、ここでは import せずに写す)。
SETTING_DEFAULTS = {
    "order_form": "multi",
    "partial_shipment": True,
    "over_shipment": "warn",
    "over_workorder": "warn",
    "receiving_required": "off",
    "over_payment": "warn",
    "order_number": "seq",
    "hidden_fields": [],
    "production_plan": True,
    "access_control": {"department": "log", "write": "log", "money": "log"},
    "assistant_model": "claude-sonnet-5-5",
    "intake_model": "claude-opus-5-5",
    "feedback_model": "claude-haiku-5-5",
    "menu": [],
}

#: 業種の見本 (src/seizou/recipes/<業種の id>.yaml) の根の鍵。答えの YAML の部分と同じ形
RECIPE_KEYS = ("industry", "item_fields", "processes", "settings")
#: 見本の工程の 1 件の鍵 (masters.services.create_process の data と同じ)
RECIPE_PROCESS_KEYS = ("code", "name", "is_outsourced", "sort_order", "use_in_plan")
#: 見本が補える設定の鍵 (業種で向きが変わる物だけ。権限・model・メニュー・隠す項目は見本に書かない)
RECIPE_SETTING_KEYS = ("order_form", "partial_shipment", "over_shipment", "over_workorder", "receiving_required", "order_number", "production_plan")
#: 見本の工程コードの形 (英大文字で始め、英大文字と数字と _ で 10 字まで。工程の表の工程コードは 10 字で一意)
RECIPE_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,9}")

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

# 会社の設定 (core の Company) の 1 行を、答えの会社名と会社の情報 (company) で作る短い Python。既にあれば触らない。
# ロゴ (答えの logo。任意) は設定の画面と同じく上げたファイルの形で update_company に渡すので、印 (favicon) も同時にできる。開発の記録 docs/nights/20261105/N166.md
CREATE_COMPANY = (
    "import importlib, json, os, pathlib; "
    "Company = importlib.import_module('seizou.core.models').Company; "
    "services = importlib.import_module('seizou.core.services'); "
    "made = not Company.objects.exists(); "
    "made and Company.objects.create(name=os.environ['SEIZOU_COMPANY_NAME']); "
    "fields = json.loads(os.environ['SEIZOU_COMPANY_FIELDS']); "
    "logo = pathlib.Path(os.environ.get('SEIZOU_COMPANY_LOGO') or '.'); "
    "logo.is_file() and fields.update(logo=importlib.import_module('django.core.files.uploadedfile').SimpleUploadedFile(logo.name, logo.read_bytes())); "
    "made and services.update_company(services.company(), fields, None); "
    "print('created' if made else 'exists')"
)

# 作り物のデータを入れる短い Python (seizou.core.demo があるときだけ。引数は種のフォルダと、相手の会社の id → 受け口の URL の頭)。
LOAD_DEMO = (
    "import importlib, pathlib; "
    "importlib.import_module('seizou.core.demo').load(pathlib.Path({seeds!r}), partner_base_urls={bases!r})"
)

#: 作り物の利用者のログイン ID (seizou.accounts.demo_seed.ACCOUNTS と同じ並び。この module は Django を読まないので写す。試験が同じことを見る)
DEMO_ACCOUNT_IDS = ("genba", "jimu", "keiri")
#: 作り物の利用者のパスワードを子の process に渡す環境変数 (seizou.accounts.demo_seed.PASSWORDS_ENV と同じ名前。値はログイン ID → パスワードの JSON)
DEMO_PASSWORDS_ENV = "SEIZOU_DEMO_PASSWORDS"
#: 種のフォルダにこのファイルがあると、作り物の利用者を入れる (seizou.core.demo の PART_SEED_MARKERS と同じ)
DEMO_ACCOUNTS_MARKER = "accounts.yaml"

# 業種の見本の工程を入れる短い Python。工程コードか名前が同じ工程が既にあれば飛ばす (種の工程が先に入っていれば種が勝つ)。
# 工程は部品マスターの services の create_process で作り (表に直接書かない = R13)、入れた数だけを最後の行に出す。
# 工程の並びは環境変数 SEIZOU_RECIPE_PROCESSES に JSON で受ける。
CREATE_PROCESSES = """\
import importlib, json, os
transaction = importlib.import_module('django.db').transaction
services = importlib.import_module('seizou.masters.services')
Process = importlib.import_module('seizou.masters.models').Process
made = 0
with transaction.atomic():
    for row in json.loads(os.environ['SEIZOU_RECIPE_PROCESSES']):
        if Process.objects.filter(code=row['code']).exists() or Process.objects.filter(name=row['name']).exists():
            continue
        services.create_process(row, None)
        made += 1
print(made)
"""


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

    # 会社の情報 (任意)。打ち違えた鍵を黙って捨てると請求書に住所が出ないので、知らない鍵は断る。
    # 登録番号の形が違うと請求書に違う番号が出るので断る (作者の決め)
    company = answers.get("company")
    if company is not None:
        if not isinstance(company, dict):
            raise AnswerError(f"company は鍵と値の並びで書きます ({'・'.join(COMPANY_KEYS)})。")
        for key, value in company.items():
            if key not in COMPANY_KEYS:
                raise AnswerError(f"company の鍵を知りません: {key}。{' / '.join(COMPANY_KEYS)} のどれかで書きます。")
            if not isinstance(value, str):
                raise AnswerError(f"company の {key} は字で書きます (数に読める値は引用符で囲む): {value!r}")
            if key == "registration_no" and value.strip() and not REGISTRATION_NO.fullmatch(value.strip()):
                raise AnswerError(f"company の registration_no は T と 13 桁の数字で書きます (例: T0000000000000): {value!r}")

    # ロゴ (任意。答えの YAML の横からの道)。無いファイルを黙って飛ばすとロゴと印 (favicon) が入らないので断る。開発の記録 docs/nights/20261105/N166.md
    logo = answers.get("logo")
    if logo is not None and not (isinstance(logo, str) and (path.parent / logo).is_file()):
        raise AnswerError(f"logo のファイルがありません: {logo!r}。答えの YAML の横からの道で、PNG か JPEG の絵のファイルを書きます。")

    # 業種の見本を入れない印 (任意)。書き損じを黙って通さないので、none の外の値は断る
    if "recipe" in answers and answers["recipe"] != "none":
        raise AnswerError(f"recipe に書けるのは none だけです (業種の見本を入れないとき): {answers['recipe']!r}")

    # --demo のつながりの種の受け口の頭 (任意)。打ち違えた URL を黙って通すと相手に届かないので、形の違う物は断る
    bases = answers.get("partner_base_urls")
    if bases is not None:
        if not isinstance(bases, dict):
            raise AnswerError("partner_base_urls は 相手の会社の id: 置き場の URL の頭 の並びで書きます (例 b_plating: https://b.example.jp)。")
        for key, value in bases.items():
            check_base_url(f"partner_base_urls の {key}", key, value)
    return answers


def check_base_url(where: str, partner_id, value) -> str:
    """相手の会社の id と置き場の URL の頭の形を見て、末尾の / を落とした頭を返す。誤りは AnswerError。

    頭は http:// か https:// で始まり、ホスト名があり、空白と ? と # を含まない (道の頭 /b のような物は持ってよい)。
    """
    if not isinstance(partner_id, str) or not ID_PATTERN.match(partner_id):
        raise AnswerError(f"{where}: 相手の会社の id の形が違います: {partner_id!r}。英小文字で始め、英小文字と数字と _ だけで書きます。")
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
    except ValueError:
        parts = None
    if (
        parts is None
        or parts.scheme not in ("http", "https")
        or not parts.netloc
        or parts.query
        or parts.fragment
        or any(char.isspace() for char in value)
    ):
        raise AnswerError(f"{where}: 置き場の URL の頭の形が違います: {value!r}。https:// で始まる URL (例 https://b.example.jp) で書きます。")
    return value.rstrip("/")


def partner_base_urls(answers: dict, environ=None) -> dict[str, str]:
    """--demo のつながりの種の受け口の頭 (相手の会社の id → 置き場の URL の頭。末尾の / は落とす)。

    答えの partner_base_urls に、環境変数 SEIZOU_PARTNER_<相手の id の大文字>_URL を重ねる (環境変数が勝つ。空の値は無い物とする)。
    誤りは AnswerError。
    """
    environ = os.environ if environ is None else environ
    bases = {
        key: check_base_url(f"partner_base_urls の {key}", key, value)
        for key, value in (answers.get("partner_base_urls") or {}).items()
    }
    for name, value in sorted(environ.items()):
        match = PARTNER_URL_ENV.fullmatch(name)
        if match and value.strip():
            partner_id = match.group(1).lower()
            bases[partner_id] = check_base_url(f"環境変数 {name}", partner_id, value.strip())
    return bases


def recipes_dir() -> Path:
    """業種の見本の置き場 (部品の中。pip で入れた版にも入る)。"""
    return Path(__file__).parent / "recipes"


def _industry_key(text) -> str:
    """業種の字をそろえる (NFKC で字の幅をそろえ、空白を全部除き、英字を小文字にする)。"""
    return "".join(unicodedata.normalize("NFKC", str(text)).split()).lower()


def _industry_words(recipe: dict) -> set[str]:
    """見本の業種の名前と言い換えを、そろえた字の集まりにする。"""
    industry = recipe["industry"]
    return {_industry_key(word) for word in [industry["name"], *(industry.get("aliases") or [])]}


def _check_recipe(name: str, recipe) -> None:
    """業種の見本 1 本の鍵と型と重なりを確かめる。誤りは AnswerError (文にファイルの名前と、どこが違うか)。"""

    def refuse(where: str, what: str) -> None:
        raise AnswerError(f"業種の見本 {name} の {where} が違います: {what}")

    if not isinstance(recipe, dict):
        refuse("形", "鍵と値の並びではありません")
    unknown = [str(key) for key in recipe if key not in RECIPE_KEYS]
    if unknown:
        refuse("根の鍵", f"知らない鍵 {'・'.join(unknown)} があります ({' / '.join(RECIPE_KEYS)} のどれかで書きます)")

    industry = recipe.get("industry")
    if not isinstance(industry, dict):
        refuse("industry", "鍵と値の並び (name と aliases) で書きます")
    if not isinstance(industry.get("name"), str) or not industry["name"].strip():
        refuse("industry の name", "空でない字で書きます")
    aliases = industry.get("aliases") or []
    if not isinstance(aliases, list) or not all(isinstance(word, str) and word.strip() for word in aliases):
        refuse("industry の aliases", "空でない字の並びで書きます")

    fields = recipe.get("item_fields") or []
    if not isinstance(fields, list):
        refuse("item_fields", "項目の並びで書きます")
    labels = set()
    for number, field in enumerate(fields, start=1):
        where = f"item_fields の {number} 番目"
        if not isinstance(field, dict):
            refuse(where, "鍵と値の並びで書きます")
        label = field.get("label")
        if not isinstance(label, str) or not label.strip():
            refuse(where, "label を空でない字で書きます")
        if field.get("type") not in FIELD_TYPES:
            refuse(where, f"型を知りません: {field.get('type')!r} ({' / '.join(FIELD_TYPES)} のどれかで書きます)")
        if label in labels:
            refuse(where, f"見出し {label} がほかの項目と重なります")
        labels.add(label)

    processes = recipe.get("processes") or []
    if not isinstance(processes, list):
        refuse("processes", "工程の並びで書きます")
    codes, titles = set(), set()
    for number, process in enumerate(processes, start=1):
        where = f"processes の {number} 番目"
        if not isinstance(process, dict) or set(process) != set(RECIPE_PROCESS_KEYS):
            refuse(where, f"鍵は {'・'.join(RECIPE_PROCESS_KEYS)} の 5 つをちょうど書きます")
        code, title, order = process["code"], process["name"], process["sort_order"]
        if not isinstance(code, str) or not RECIPE_CODE_PATTERN.fullmatch(code):
            refuse(where, f"工程コード {code!r} は英大文字で始め、英大文字と数字と _ で 10 字までで書きます")
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            refuse(where, "name は 1〜100 字の字で書きます")
        for flag in ("is_outsourced", "use_in_plan"):
            if not isinstance(process[flag], bool):
                refuse(where, f"{flag} は true か false で書きます")
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            refuse(where, "sort_order は 0 以上の整数で書きます")
        if code in codes:
            refuse(where, f"工程コード {code} がほかの工程と重なります")
        if title in titles:
            refuse(where, f"工程の名前 {title} がほかの工程と重なります")
        codes.add(code)
        titles.add(title)

    settings = recipe.get("settings") or {}
    if not isinstance(settings, dict):
        refuse("settings", "鍵と値の並びで書きます")
    unknown = [str(key) for key in settings if key not in RECIPE_SETTING_KEYS]
    if unknown:
        refuse("settings", f"見本に書けない鍵 {'・'.join(unknown)} があります ({' / '.join(RECIPE_SETTING_KEYS)} のどれかで書きます)")


def load_recipes(folder: Path) -> dict:
    """folder の業種の見本 (*.yaml) を名前の順に読み、{業種の id (ファイル名から拡張子を除いた物): 見本} を返す。

    1 本ずつ鍵と型と重なりを確かめ、見本どうしで業種の名前と言い換えが重ならないことも見る。誤りは AnswerError。
    設定の値が選べる値かは見ない (試験が見る)。README.md は読まない。
    """
    recipes = {}
    owners = {}  # そろえた業種の字 → その字を持つ見本のファイルの名前
    for path in sorted(folder.glob("*.yaml")):
        try:
            recipe = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise AnswerError(f"業種の見本 {path.name} を読めません。") from exc
        _check_recipe(path.name, recipe)
        for word in sorted(_industry_words(recipe)):
            if word in owners:
                raise AnswerError(f"業種の見本 {owners[word]} と {path.name} の業種の名前か言い換えが重なります: {word}")
            owners[word] = path.name
        recipes[path.stem] = recipe
    return recipes


def find_recipe(answers: dict, recipes: dict) -> tuple[str, dict] | None:
    """答えの industry に当たる業種の見本の (id, 見本)。recipe: none・industry が無い・どれにも当たらないときは None。

    字の幅と空白と英字の大小をそろえた後で、見本の名前か言い換えのどれかと全部が同じときだけ当たる。
    """
    if answers.get("recipe") == "none" or not answers.get("industry"):
        return None
    key = _industry_key(answers["industry"])
    for recipe_id, recipe in recipes.items():
        if key in _industry_words(recipe):
            return recipe_id, recipe
    return None


def apply_recipe(answers: dict, recipe: dict) -> tuple[dict, dict]:
    """答えに無い項目 (見出しで比べる) と設定 (鍵で比べる) を見本から補った答えの写しと、足した物を返す。

    答えの辞書は書き換えない。項目は答えの項目の後ろに見本の順で足す (答えの項目の列の名前 ext01… は変わらない)。
    答えの settings が辞書でなければ設定は触らない (後の settings_with_defaults が断る)。
    足した物は {"項目": n, "設定": k, "工程": [見本の工程の並び]} (工程は命令が、既にある物を飛ばして入れる)。
    """
    merged = dict(answers)
    given_fields = answers.get("item_fields") or []
    labels = {field.get("label") for field in given_fields}
    extra_fields = [dict(field) for field in recipe.get("item_fields") or [] if field["label"] not in labels]
    if extra_fields:
        merged["item_fields"] = [*given_fields, *extra_fields]

    added_settings = 0
    given_settings = answers.get("settings") or {}
    if isinstance(given_settings, dict):
        settings = dict(given_settings)
        for key, value in (recipe.get("settings") or {}).items():
            if key not in settings:
                settings[key] = value
                added_settings += 1
        if added_settings:
            merged["settings"] = settings

    processes = [dict(process) for process in recipe.get("processes") or []]
    return merged, {"項目": len(extra_fields), "設定": added_settings, "工程": processes}


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
    """拡張の表の 1 列。文字の上限は 100 (表の設計の決め)。必須でなければ空を許す。"""
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
    values = {"id": answers["id"], "industry": str(answers.get("industry") or ""), **settings, "port": answers["port"]}
    return "\n".join(f"    {key!r}: {value!r}," for key, value in values.items())


def _on_windows() -> bool:
    """Windows の上で動いているか (起動の 1 行の形を PowerShell に合わせるため)。試験はこの関数を差し替える。"""
    return os.name == "nt"


def manage_line(command: str) -> str:
    """置き場で打つ manage.py の 1 行 (<python> manage.py <command>)。python はこの命令を動かす python (sys.executable)。

    macOS と Linux では、python の道に空白があるときだけ "…" で囲む。Windows では、python の道をいつも "…" で囲み、頭に & を置く
    (PowerShell は引用符で始まる字を命令と読まず、& があれば命令として打つ)。
    """
    python = sys.executable
    if _on_windows():
        return f'& "{python}" manage.py {command}'
    if " " in python:
        python = f'"{python}"'
    return f"{python} manage.py {command}"


def start_line(site: Path, port: int) -> str:
    """起動の 1 行 (seizou new の「次にすること」と、置き場の README.md の「起動」に出す)。

    macOS と Linux では cd <置き場> && <python> manage.py runserver <port> (置き場の道に空白があるときだけ "…" で囲む)。
    Windows では cd "<置き場>"; & "<python>" manage.py runserver <port> (Windows PowerShell 5.1 は && を区切りと読まないので ; で分ける)。
    """
    run = manage_line(f"runserver {port}")
    place = str(site)
    if _on_windows():
        return f'cd "{place}"; {run}'
    if " " in place:
        place = f'"{place}"'
    return f"cd {place} && {run}"


def write_secret_file(path: Path, text: str) -> None:
    """持ち主だけが読める形で書く (Windows では権限を無視する)。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def append_secret_lines(path: Path, lines: list[str]) -> None:
    """持ち主だけが読めるファイルの末尾に行を足す (無ければ作る。末尾が改行でなければ先に改行を足す。Windows では権限を無視する)。"""
    text = "".join(f"{line}\n" for line in lines)
    if path.is_file() and path.stat().st_size and not path.read_bytes().endswith(b"\n"):
        text = "\n" + text
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as handle:
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
    # git に入れない物 (.env・db.sqlite3・media/ ほか)。既にあれば書き直さない。決め 11-06 00:0x。開発の記録 docs/nights/20261106/N172.md
    write_gitignore(site)
    # Claude Code に .env・DB・控えを読ませない設定 (.claude/settings.json)。既にあれば書き直さない。決め 11-08 00:5x
    write_claude_settings(site)
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
    # AI が守る決まり (CLAUDE.md) と、Claude 以外の道具向けの短い版 (AGENTS.md)。決め 11-05 17:2x。開発の記録 docs/nights/20261105/N165.md
    write_ai_docs(site, company_id, str(answers["name"]))
    (site / "checks.py").write_text(render("checks.py.tmpl", id=company_id), encoding="utf-8")
    (site / "deploy").mkdir(parents=True, exist_ok=True)
    (site / "deploy" / "nginx-files.conf").write_text(
        render("nginx-files.conf.tmpl", id=company_id, media_root=(site.resolve() / "media").as_posix()),
        encoding="utf-8",
    )
    for folder in ("templates", "static/css", "manual/images"):
        (site / folder).mkdir(parents=True, exist_ok=True)


def write_ai_docs(site: Path, company_id: str, name: str) -> None:
    """置き場に、AI が守る決まり (CLAUDE.md) と短い版 (AGENTS.md) を書く (seizou new と seizou ai-docs が呼ぶ)。開発の記録 docs/nights/20261105/N165.md・docs/nights/20261105/N170.md"""
    (site / "CLAUDE.md").write_text(render("CLAUDE.md.tmpl", id=company_id, name=name), encoding="utf-8")
    (site / "AGENTS.md").write_text(render("AGENTS.md.tmpl", id=company_id, name=name), encoding="utf-8")


def write_gitignore(site: Path) -> bool:
    """置き場に .gitignore (git に入れない物 = 鍵と DB と上げたファイル) を書く。既にあれば触らない (人が直した物を消さない)。書いたら True。開発の記録 docs/nights/20261106/N172.md"""
    path = site / ".gitignore"
    if path.exists():
        return False
    path.write_text(render("gitignore.tmpl"), encoding="utf-8")
    return True


def write_claude_settings(site: Path) -> bool:
    """置き場に .claude/settings.json (Claude Code の permissions.deny) を書く。既にあれば触らない。書いたら True。

    訳: 会社の置き場で開いた Claude Code が、注入の字を含む注文書や会話を読んだ後に、.env (SECRET_KEY・取引先の鍵・
    API の鍵・GitHub のトークン) と db.sqlite3 と backups/ を読んで外へ出す流れを、AI のうっかりの読みの段で止める。
    道は / で始まる形 (settings.json を置いた置き場の直下から数える)。Write は Read の deny で止まるので書かず、
    Edit(/.env) は NotebookEdit のために残す。Bash(*seizou_secret*) は、鍵の値を作って画面に出す命令を AI に打たせない。
    限り: shell (Bash の道具の cat や python)・manage.py diffsettings・grep -r・子の process の読みは止まらない (SECURITY.md)。
    既にある settings.json は会社か人が直した物なので、--force でも書き直さない (黙って消さない)。
    出所: 本人の決め 11-08 00:5x (hook に Read は足さず、置き場の deny で止める) と、仕組みの調べを受けた作者の決め。
    変えてよい条件: 本人が決め直したとき、または Claude Code の permissions の書き方が変わったとき。
    """
    path = site / ".claude" / "settings.json"
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render("settings.json.tmpl"), encoding="utf-8")
    return True


def register_site_for_plugin(site: Path) -> None:
    """置き場を、プラグインの MCP が起こしてよい置き場の一覧に、この python (sys.executable) で登録し、1 行を出す。

    訳: プラグインの起こし役 (plugin/mcp/start.py) は、開いたフォルダの README.md を読まず、この一覧にある置き場だけを
    一覧の python で起こす。人から受け取ったフォルダを開いただけで、その中の実行ファイルと manage.py が動くのを止めるため。
    python は README.md の起動の 1 行 (start_line) と同じ sys.executable。
    一覧に書けないときと、今の一覧が壊れているときは、置き場づくりを止めずに標準エラーへ 1 行を出す (MCP がこの置き場で立たないだけなので)。
    出所: 本人の決め 11-08 01:0x と、Claude Security の走査の指摘。変えてよい条件: 本人が決め直したとき。
    """
    try:
        path = site_registry.register_site(site)
    except (OSError, ValueError) as exc:
        print(
            f"置き場の一覧に登録できませんでした ({exc})。プラグインの MCP はこの置き場で立ちません。直した後に seizou ai-docs {site} を打つと登録します。",
            file=sys.stderr,
        )
        return
    print(f"登録した: {site_registry.site_key(site)} (プラグインの MCP が起こす置き場の一覧 {path})")


def copy_manual(seeds: Path, site: Path) -> int:
    """種のフォルダの manual/ にある画面ごとの説明書の断片 (*.html) を、会社の置き場の manual/ に写す。写した数を返す。

    種に manual/ が無ければ何もせず 0。置き場に同じ名前のファイルがあれば写さない (上書きしない)。
    写すのは manual/ の直下の .html だけ (絵の images/ は種に置かない)。copyfile なので最終更新は写した時刻
    (使い方の冊子の表紙の日付は写した日になる)。
    """
    return _copy_fragments(seeds / "manual", site)


def manual_default_dir() -> Path:
    """画面ごとの説明書の断片の標準の組の置き場 (部品の中。pip で入れた版にも入る)。"""
    return Path(__file__).parent / "core" / "manual_default"


def copy_manual_default(site: Path) -> int:
    """標準の組 (manual_default_dir の *.html) を、会社の置き場の manual/ に写す。写した数を返す。

    copy_manual と同じく、置き場に同じ名前のファイルがあれば写さない (--demo の種の断片と、人が直した断片が勝つ)。
    """
    return _copy_fragments(manual_default_dir(), site)


def _copy_fragments(source: Path, site: Path) -> int:
    """source の直下の *.html を置き場の manual/ に写す。source が無ければ何もせず 0。同じ名前は上書きしない。"""
    if not source.is_dir():
        return 0
    target = site / "manual"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(source.glob("*.html")):
        destination = target / path.name
        if destination.exists():
            continue
        shutil.copyfile(path, destination)
        copied += 1
    return copied


def child_env(extra: dict | None = None) -> dict:
    # 代入にする。親 (pytest-django など) の DJANGO_SETTINGS_MODULE を子に引き継がない。
    # 子の標準出力と標準エラーを UTF-8 にそろえる (run_manage が UTF-8 で読むため。Windows の管の既定は cp932 か cp1252)。
    return {**os.environ, "DJANGO_SETTINGS_MODULE": "settings", "PYTHONIOENCODING": "utf-8", **(extra or {})}


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


def demo_passwords(choice: str | None) -> dict:
    """--demo-password の値から ログイン ID → パスワード を作る (決め 11-04 15:0x)。

    random なら 1 人ずつ乱数 (管理者と同じ作り方)。ほかの字なら 3 人ともその字。None なら空 (公開の合言葉のまま)。
    """
    if not choice:
        return {}
    if choice == "random":
        return {login_id: secrets.token_urlsafe(12) for login_id in DEMO_ACCOUNT_IDS}
    return dict.fromkeys(DEMO_ACCOUNT_IDS, choice)


def show_failure(step: str, result: subprocess.CompletedProcess) -> int:
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    print(f"止まりました: {step} が失敗しました (終わりの番号 {result.returncode})。上の出力を見てください。", file=sys.stderr)
    return EXIT_CHILD


def command_new(answers_path: Path, demo: bool, password_file: Path | None = None, demo_password: str | None = None) -> int:
    print("答えの YAML から会社のプロジェクトを作ります。")
    print("置き場にファイルを書き、データベースを用意し (migrate)、管理者を 1 人作ります。起動はしません。")

    try:
        answers, recipe_found, recipe_added, settings, used_defaults, columns, field_defaults, bases = _new_answers(
            answers_path, demo, password_file
        )
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
    failure = _new_files(site, answers_path, answers, settings, columns)
    if failure is not None:
        return failure
    admin_lines, failure = _new_admin(site, password_file)
    if failure is not None:
        return failure
    failure = _new_company(site, answers, answers_path)
    if failure is not None:
        return failure
    seed_copied, made_passwords, failure = _new_demo(site, demo, answers, answers_path, bases, demo_password)
    if failure is not None:
        return failure

    # 説明書の断片の標準の組は --demo の有無に関わらず写す (種の後なので、同じ名前は種の断片が勝つ)
    default_copied = copy_manual_default(site)
    if seed_copied or default_copied:
        print(f"説明書の断片を写しました: {seed_copied + default_copied} 件 (標準 {default_copied}・種 {seed_copied}。{site / 'manual'})")

    # 業種の見本の工程は種の後に入れる (工程コードか名前が同じ工程を飛ばすので、種の工程と種の出力の行が勝つ)
    failure = _new_recipe_processes(site, recipe_found, recipe_added)
    if failure is not None:
        return failure

    _new_report(site, answers["port"], admin_lines, made_passwords, demo_password, password_file)
    return EXIT_OK


def _new_answers(answers_path: Path, demo: bool, password_file: Path | None) -> tuple:
    """seizou new の段 (答えを読む)。業種の見本で補い、設定と項目と相手の URL の頭を作る。答えの誤りは AnswerError。

    返す物 = (answers, recipe_found, recipe_added, settings, used_defaults, columns, field_defaults, bases)。
    """
    answers = load_answers(answers_path)
    # 業種の見本 (任意)。答えの industry が見本に当たれば、答えに無い項目と設定を見本から補った答えに置き換える
    recipe_found = None
    recipe_added = {"項目": 0, "設定": 0, "工程": []}
    if answers.get("recipe") != "none" and answers.get("industry"):
        recipe_found = find_recipe(answers, load_recipes(recipes_dir()))
    if recipe_found:
        answers, recipe_added = apply_recipe(answers, recipe_found[1])
    settings, used_defaults = settings_with_defaults(answers)
    columns, field_defaults = item_columns(answers)
    bases = partner_base_urls(answers) if demo else {}
    if password_file is not None and not password_file.parent.is_dir():
        raise AnswerError(f"--password-file のフォルダがありません: {password_file.parent}")
    return answers, recipe_found, recipe_added, settings, used_defaults, columns, field_defaults, bases


def _new_files(site: Path, answers_path: Path, answers: dict, settings: dict, columns: list[dict]) -> int | None:
    """seizou new の段 (ファイルを書く)。置き場のファイルと会社ごとの項目の表を書き、migrate する。子の process が失敗したら終わりの番号。"""
    write_project(site, answers_path, answers, settings)
    print(f"ファイルを書きました: {site}")
    # プラグインの MCP が起こしてよい置き場の一覧に登録する (README の起動の 1 行と同じ python)。決め 11-08 01:0x
    register_site_for_plugin(site)

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
    return None


def _new_admin(site: Path, password_file: Path | None) -> tuple[list[str], int | None]:
    """seizou new の段 (管理者)。管理者を 1 人作り、「できた物」の後に出す行を返す。子の process が失敗したら ([], 終わりの番号)。"""
    password = secrets.token_urlsafe(12)
    result = run_manage(
        site,
        ["shell", "--no-imports", "-c", CREATE_ADMIN],
        {"SEIZOU_ADMIN_ID": ADMIN_ID, "SEIZOU_ADMIN_PASSWORD": password},
    )
    if result.returncode != 0:
        return [], show_failure("管理者を作る所", result)
    # ID とパスワードは 1 行ずつ (機械で拾う人が行の頭で探せる)。「管理者のパスワード: 」の行は値を出すときだけ
    admin_lines = [f"管理者の ID: {ADMIN_ID}"]
    if result.stdout.strip().splitlines()[-1:] == ["exists"]:
        admin_lines.append("管理者は既にあったので作らず、パスワードも変えていません。")
    elif password_file is not None:
        # 画面と記録に値を残さない。後の段が止まっても控えが残るよう、作った直後に書く
        write_secret_file(password_file, f"{ADMIN_ID}:{password}\n")
        admin_lines.append(f"管理者のパスワードを書いたファイル: {password_file} (画面には出しません。持ち主だけが読めます)")
    else:
        admin_lines.append(f"管理者のパスワード: {password}")
        admin_lines.append("パスワードはこの 1 度だけ出します。")
    return admin_lines, None


def _new_company(site: Path, answers: dict, answers_path: Path) -> int | None:
    """seizou new の段 (会社の設定)。画面の上の帯に出る会社名の 1 行を、答えの name で作る。子の process が失敗したら終わりの番号。"""
    logo = str(answers_path.parent / answers["logo"]) if answers.get("logo") else ""
    result = run_manage(
        site,
        ["shell", "--no-imports", "-c", CREATE_COMPANY],
        {"SEIZOU_COMPANY_NAME": str(answers["name"]), "SEIZOU_COMPANY_FIELDS": json.dumps(answers.get("company") or {}, ensure_ascii=False), "SEIZOU_COMPANY_LOGO": logo},
    )
    if result.returncode != 0:
        return show_failure("会社の設定を作る所", result)
    print(f"会社の設定を作りました: {answers['name']}" + ("。ロゴと印 (favicon) も入れました" if logo else ""))
    return None


def _new_demo(
    site: Path, demo: bool, answers: dict, answers_path: Path, bases: dict, demo_password: str | None
) -> tuple[int, dict, int | None]:
    """seizou new の段 (--demo の作り物のデータ)。返す物 = (写した種の説明書の断片の数, 作り物の利用者のパスワード, 失敗の終わりの番号か None)。"""
    if not demo:
        return 0, {}, None
    # import せずに有無だけを見る (この process で Django の model を読まないため)。
    if importlib.util.find_spec("seizou.core.demo") is None:
        print("作り物のデータの読み込みは後の版です (seizou.core.demo が無い)。")
        return 0, {}, None
    # 種は答えの YAML と同じ場所の <id>/ (examples/companies/README.md の約束)。
    seeds = answers_path.parent / answers["id"]
    # 作り物の利用者のパスワード (--demo-password)。種に作り物の利用者の印があるときだけ作り、子の process に環境変数で渡す
    made_passwords = demo_passwords(demo_password) if (seeds / DEMO_ACCOUNTS_MARKER).is_file() else {}
    demo_env = {DEMO_PASSWORDS_ENV: json.dumps(made_passwords)} if made_passwords else None
    if bases:
        shown = "、".join(f"{key} → {value}" for key, value in sorted(bases.items()))
        print(f"つながりの種の受け口の URL の頭を、相手の置き場の URL にします: {shown}")
    result = run_manage(site, ["shell", "--no-imports", "-c", LOAD_DEMO.format(seeds=str(seeds), bases=bases)], demo_env)
    if result.returncode != 0:
        return 0, made_passwords, show_failure("作り物のデータの読み込み", result)
    # 読み込みの出力 (種類ごとの「入れた n 件・飛ばした m 件」と「注意」の行) をそのまま画面に出す
    sys.stdout.write(result.stdout or "")
    print(f"作り物のデータを登録しました: {seeds}")
    return copy_manual(seeds, site), made_passwords, None


def _new_recipe_processes(site: Path, recipe_found, recipe_added: dict) -> int | None:
    """seizou new の段 (業種の見本の工程)。見本の工程を入れ、見本から入れた物の数を出す。子の process が失敗したら終わりの番号。"""
    recipe_processes = 0
    if recipe_found and recipe_added["工程"] and fields_installed():
        result = run_manage(
            site,
            ["shell", "--no-imports", "-c", CREATE_PROCESSES],
            {"SEIZOU_RECIPE_PROCESSES": json.dumps(recipe_added["工程"], ensure_ascii=False)},
        )
        if result.returncode != 0:
            return show_failure("業種の見本の工程を入れる所", result)
        last = result.stdout.strip().splitlines()[-1:]
        recipe_processes = int(last[0]) if last and last[0].isdigit() else 0
    if recipe_found:
        recipe_name = recipe_found[1]["industry"]["name"]
        if recipe_added["項目"] or recipe_processes or recipe_added["設定"]:
            print(
                f"業種の見本 ({recipe_name}) から入れました: "
                f"項目 {recipe_added['項目']}・工程 {recipe_processes}・設定 {recipe_added['設定']}。"
                "工程と設定は画面で直せます。見本を入れずに作り直すときは、答えの YAML に recipe: none と書きます。"
            )
        else:
            print(f"業種の見本 ({recipe_name}) と同じ物が全部あったので、足した物はありません。")
    return None


def _new_report(
    site: Path, port: int, admin_lines: list[str], made_passwords: dict, demo_password: str | None, password_file: Path | None
) -> None:
    """seizou new の段 (できた物)。管理者と作り物の利用者のパスワードの行と、起動・点検・練習の 1 行ずつを出す。"""
    print(f"できた物: {site} の会社のプロジェクト。")
    for line in admin_lines:
        print(line)
    # 作り物の利用者のパスワード (--demo-password random)。管理者と同じく 1 人 1 行で出す。渡された値は出さない
    if made_passwords and demo_password == "random" and password_file is not None:
        append_secret_lines(password_file, [f"{login_id}:{value}" for login_id, value in made_passwords.items()])
        print(f"作り物の利用者 {len(made_passwords)} 人のパスワードを書いたファイル: {password_file} (1 人 1 行で足しました。画面には出しません)")
    elif made_passwords and demo_password == "random":
        for login_id, value in made_passwords.items():
            print(f"作り物の利用者 {login_id} のパスワード: {value}")
    elif made_passwords:
        print(f"作り物の利用者 {len(made_passwords)} 人のパスワード: --demo-password で渡した値にしました。")
    print(f"次にすること: {start_line(site, port)} で起動し、http://127.0.0.1:{port}/login/ を開いてログインします。")
    print("点検: 同じ置き場で、上の 1 行と同じ python の manage.py seizou_doctor を打つと、直す物を直し方つきで出します (何も書き換えません)。")
    print(
        f"練習するときは: 置き場で {manage_line('seizou_practice start')} を打つ"
        " (今のデータの写しを使うサーバーを別のポートで立てる。ここでの登録は本番に残らず、取引先へは送らない。閉じるときは同じ python で seizou_practice stop)。"
    )


def command_ai_docs(site: Path, force: bool = False) -> int:
    """seizou ai-docs (決め 11-05 22:5x。開発の記録 docs/nights/20261105/N170.md)。既にある会社の置き場に、CLAUDE.md と AGENTS.md を後から書く。

    置き場は manage.py と answers.yaml があるフォルダ。会社の id と名前は置き場の answers.yaml の id と name
    (write_project と同じ値)。load_answers は使わない (logo の道は元の答えの YAML の横からなので、置き場の写しでは断られる)。
    どちらかが既にあれば、--force のときだけ 2 つとも書き直す (人が直した決まりを黙って消さない)。誤りは何も書かずに終わりの番号 2。
    .gitignore は無いときだけ書き、書いたときだけ「書いた:」の行に名前を足す (既にある .gitignore は --force でも書き直さない)。
    .claude/settings.json (write_claude_settings) は、CLAUDE.md と AGENTS.md が既にあって止まる前に、無いときだけ書く
    (この版より前に作った置き場にも届かせるため。決め 11-08 00:5x)。書いたときは止まる前に「書いた:」の行を 1 行出す。
    置き場を、プラグインの MCP が起こしてよい置き場の一覧に登録する (register_site_for_plugin)。これも止まる前で、
    版を上げた後に CLAUDE.md のある置き場で打っても登録だけは済む (決め 11-08 01:0x)。
    """
    answers_path = site / "answers.yaml"
    if not ((site / "manage.py").is_file() and answers_path.is_file()):
        print(f"止まりました: 置き場ではありません: {site} (manage.py か answers.yaml がありません)", file=sys.stderr)
        return EXIT_INPUT
    try:
        answers = yaml.safe_load(answers_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        answers = None
    company_id = answers.get("id") if isinstance(answers, dict) else None
    if not (isinstance(company_id, str) and ID_PATTERN.match(company_id) and answers.get("name") not in (None, "")):
        print(f"止まりました: 置き場の answers.yaml から会社の id と name を読めません: {answers_path}", file=sys.stderr)
        return EXIT_INPUT
    # 止まる前に書く (CLAUDE.md が既にある置き場にも届かせる)。既にあれば触らない
    if write_claude_settings(site):
        print(f"書いた: {site / '.claude' / 'settings.json'}")
    register_site_for_plugin(site)
    existing = [name for name in ("CLAUDE.md", "AGENTS.md") if (site / name).exists()]
    if existing and not force:
        print(f"止まりました: 既にあります: {'・'.join(existing)} (上書きするなら --force)", file=sys.stderr)
        return EXIT_INPUT
    write_ai_docs(site, company_id, str(answers["name"]))
    wrote_gitignore = write_gitignore(site)
    print(f"書いた: {site / 'CLAUDE.md'}・AGENTS.md" + ("・.gitignore" if wrote_gitignore else ""))
    return EXIT_OK


def command_github_token(site: Path) -> int:
    """seizou github-token (開発の記録 docs/nights/20261106/N185.md。決め 11-06 21:2x)。会社の置き場の .env に、GitHub のトークン・送り先・口座の名前の 3 行を書く。

    画面の process が置き場の .env に書けない置き場のための道。置き場は manage.py と answers.yaml があるフォルダ。
    トークンは getpass で聞き (画面に出さない)、送り先を聞き (空なら既定)、GitHub に口座の名前を聞いて確かめてから書く。
    出すのは「<口座の名前> の口座としてつながりました。」だけ (トークンの値は出さない)。誤りは何も書かずに終わりの番号 2。
    Django は起こさない (seizou.feedback.github の .env の関数に道を渡す。github が Django の settings を読むのは env_path の中だけ)。
    """
    if not ((site / "manage.py").is_file() and (site / "answers.yaml").is_file()):
        print(f"止まりました: 置き場ではありません: {site} (manage.py か answers.yaml がありません)", file=sys.stderr)
        return EXIT_INPUT
    from seizou.feedback import github

    try:
        token = github.check_token(getpass.getpass("トークン (画面に出ません): ").strip())
        answer = input(f"送り先のリポジトリ (owner/name。空なら {github.DEFAULT_REPO}): ").strip()
        repo = github.check_repo(answer or github.DEFAULT_REPO)
        login = github.whoami(token)
    except (EOFError, KeyboardInterrupt):
        print("止まりました: 入力がありませんでした。何も書いていません。", file=sys.stderr)
        return EXIT_INPUT
    except github.GithubError as exc:
        print(f"止まりました: {exc} 何も書いていません。", file=sys.stderr)
        return EXIT_INPUT
    path = site / ".env"
    try:
        for key, value in ((github.TOKEN_ENV, token), (github.REPO_ENV, repo), (github.LOGIN_ENV, login)):
            github.write_env_value(key, value, path=path)
    except github.GithubError:
        print(f"止まりました: {path} に書けませんでした。置き場の持ち主の口座で打ってください。", file=sys.stderr)
        return EXIT_INPUT
    print(f"{login} の口座としてつながりました。")
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
    new.add_argument(
        "--demo-password",
        metavar="random|<値>",
        help="--demo の作り物の利用者 3 人のパスワード。random なら 1 人ずつ乱数を作って 1 人 1 行で出す。既定は docs/getting-started.md の公開の合言葉",
    )
    new.add_argument(
        "--password-file",
        type=Path,
        metavar="PATH",
        help="管理者のパスワードを画面に出さず、このファイルに admin:<値> の 1 行で書く (持ち主だけが読める)",
    )
    ai_docs = commands.add_parser(
        "ai-docs",
        help="既にある会社の置き場に、AI が守る決まりの CLAUDE.md と AGENTS.md を書く",
        description="既にある会社の置き場 (manage.py と answers.yaml があるフォルダ) に、seizou new と同じ CLAUDE.md と AGENTS.md を後から書く。",
    )
    ai_docs.add_argument("site", type=Path, help="会社の置き場のフォルダ (例 ~/seizou-sites/<id>)")
    ai_docs.add_argument("--force", action="store_true", help="CLAUDE.md か AGENTS.md が既にあっても、2 つとも書き直す")
    github_token = commands.add_parser(
        "github-token",
        help="会社の置き場の .env に、フィードバックを GitHub に送るトークンを書く (画面に出さずに聞く)",
        description="会社の置き場の .env に、フィードバックを GitHub に送るトークン・送り先・口座の名前の 3 行を書く。トークンは画面に出さずに聞く。",
    )
    github_token.add_argument("site", type=Path, nargs="?", default=Path("."), help="会社の置き場のフォルダ (既定は今のフォルダ)")
    return parser


def main(argv: list[str] | None = None) -> int:
    # 標準出力と標準エラーの文字コードで表せない字を ? にして書く (Windows の管の cp932 や cp1252 で、日本語の出力で落ちないように)。
    # 文字コードは変えない (管の読み手の文字コードのまま書く)。--help の日本語も守るので、引数を読む前に行う
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "new":
        return command_new(args.answers, args.demo, args.password_file, demo_password=args.demo_password)
    if args.command == "ai-docs":
        return command_ai_docs(args.site, args.force)
    if args.command == "github-token":
        return command_github_token(args.site)
    return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
