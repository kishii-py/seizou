"""examples/companies/ の 2 社の答えの YAML と、作り物のデータの種の形を確かめる。

種の行の数は見ない (行は後で足すため)。見るのは、答えの鍵と値の約束、
種のファイルの見出しと鍵、つながりに鍵の値が書かれていないことだけ。
"""

import csv
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

yaml = pytest.importorskip(
    "yaml",
    reason="答えの YAML を読む PyYAML (pyyaml) が入っていない。"
    "`uv run --no-project --with pytest --with pyyaml pytest` で回す",
)

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
COMPANY_IDS = ("a_machining", "b_plating")
OTHER_COMPANY = {"a_machining": "b_plating", "b_plating": "a_machining"}

ANSWER_KEYS = {"id", "name", "industry", "item_fields", "settings", "port"}
ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
EXPECTED_PORTS = {"a_machining": 8001, "b_plating": 8002}

SETTING_VALUES = {
    "order_form": {"multi", "single"},
    "over_shipment": {"warn", "stop", "off"},
    "over_workorder": {"warn", "stop", "off"},
    "order_number": {"seq", "ym_seq"},
}
BOOL_SETTINGS = {"partial_shipment", "production_plan"}

FIELD_KEYS = {"label", "type", "required", "in_list", "searchable"}
FIELD_TYPES = {"text", "number", "date", "choice", "bool"}
EXPECTED_FIELD_LABELS = {
    "a_machining": ["材質", "図面の版"],
    "b_plating": ["処理の種類", "膜厚"],
}
NUMBER_FIELDS = {"膜厚"}

SEED_FILES = (
    "items.csv",
    "partners.csv",
    "partner_items.csv",
    "processes.yaml",
    "orders.yaml",
    "connections.yaml",
)
ITEM_HEADER = ["自社品番", "品名", "図面番号", "図面ファイル", "単位", "標準単価", "備考"]
PARTNER_HEADER = [
    "取引先コード",
    "名前",
    "略称",
    "取引先の種類",
    "締日",
    "支払の月",
    "支払日",
    "小計の計算方法",
    "消費税の計算方法",
    "備考",
]
PARTNER_ITEM_HEADER = ["取引先コード", "自社品番", "取引先の品番", "取引先の品名"]
SEED_YAML_ROOT_KEYS = {
    "processes.yaml": "工程",
    "orders.yaml": "受注",
    "connections.yaml": "つながり",
}

CONNECTION_KEYS = {"取引先コード", "受け口の URL", "鍵の置き場の名前", "向き"}
SECRET_ENV_PATTERN = re.compile(r"SEIZOU_PARTNER_([A-Z0-9_]+)_SECRET")
BLANK_MARK_PATTERN = re.compile(r"\[空欄: [^\]]+\]")
BOM = b"\xef\xbb\xbf"


def load_answers(company_id):
    with open(COMPANIES_DIR / f"{company_id}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seed_yaml(company_id, name):
    with open(COMPANIES_DIR / company_id / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_csv_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_answers_have_exact_keys_and_id_matches_file_name(company_id):
    answers = load_answers(company_id)

    assert set(answers) == ANSWER_KEYS
    assert answers["id"] == company_id
    # 環境変数の名前 (SEIZOU_PARTNER_<ID>_SECRET) に大文字にして使えること
    assert ID_PATTERN.fullmatch(answers["id"])
    assert isinstance(answers["name"], str) and answers["name"].strip()
    assert isinstance(answers["industry"], str) and answers["industry"].strip()


def test_ports_are_8001_and_8002_and_differ():
    ports = {company_id: load_answers(company_id)["port"] for company_id in COMPANY_IDS}

    assert ports == EXPECTED_PORTS
    assert len(set(ports.values())) == len(COMPANY_IDS)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_settings_have_five_keys_and_hidden_fields(company_id):
    settings = load_answers(company_id)["settings"]

    assert set(settings) == set(SETTING_VALUES) | BOOL_SETTINGS | {"hidden_fields"}
    for key, allowed in SETTING_VALUES.items():
        assert settings[key] in allowed, key
    for key in BOOL_SETTINGS:
        assert isinstance(settings[key], bool), key
    assert settings["hidden_fields"] == []


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_item_fields_follow_the_answers(company_id):
    fields = load_answers(company_id)["item_fields"]

    assert [field["label"] for field in fields] == EXPECTED_FIELD_LABELS[company_id]
    for field in fields:
        assert set(field) == FIELD_KEYS, field["label"]
        assert field["type"] in FIELD_TYPES, field["label"]
        for key in ("required", "in_list", "searchable"):
            assert isinstance(field[key], bool), (field["label"], key)
        # 一覧に列が出ること (2 社で列が違って見える)
        assert field["in_list"] is True, field["label"]
        expected_type = "number" if field["label"] in NUMBER_FIELDS else "text"
        assert field["type"] == expected_type, field["label"]
    # 検索に使う項目が各社 1 つ以上あること
    assert any(field["searchable"] for field in fields)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
@pytest.mark.parametrize("name", SEED_FILES)
def test_seed_file_exists(company_id, name):
    assert (COMPANIES_DIR / company_id / name).is_file()


@pytest.mark.parametrize("company_id", COMPANY_IDS)
@pytest.mark.parametrize("name", sorted(SEED_YAML_ROOT_KEYS))
def test_seed_yaml_holds_one_list(company_id, name):
    data = load_seed_yaml(company_id, name)

    assert set(data) == {SEED_YAML_ROOT_KEYS[name]}
    assert isinstance(data[SEED_YAML_ROOT_KEYS[name]], list)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_seed_csv_headers(company_id):
    labels = [field["label"] for field in load_answers(company_id)["item_fields"]]
    expected = {
        "items.csv": ITEM_HEADER + labels,
        "partners.csv": PARTNER_HEADER,
        "partner_items.csv": PARTNER_ITEM_HEADER,
    }

    for name, header in expected.items():
        path = COMPANIES_DIR / company_id / name
        assert path.read_bytes().startswith(BOM), name
        rows = read_csv_rows(path)
        # 行の数は見ない (行は後で足す)
        assert rows and rows[0] == header, name


def partner_rows(company_id):
    rows = read_csv_rows(COMPANIES_DIR / company_id / "partners.csv")
    return {row[0]: dict(zip(rows[0], row, strict=True)) for row in rows[1:]}


def test_a_machining_connects_to_b_plating_without_secret_value():
    connections = load_seed_yaml("a_machining", "connections.yaml")["つながり"]
    b_port = load_answers("b_plating")["port"]

    assert len(connections) == 1
    connection = connections[0]
    # 鍵の値を入れる欄が無いこと
    assert set(connection) == CONNECTION_KEYS
    assert connection["向き"] == "送る"

    secret_env = SECRET_ENV_PATTERN.fullmatch(connection["鍵の置き場の名前"])
    assert secret_env is not None
    assert secret_env.group(1) == OTHER_COMPANY["a_machining"].upper()

    url = urlsplit(connection["受け口の URL"])
    assert url.scheme == "http"
    assert url.port == b_port
    # 受け口のパスは connect の受け口 (D15)。空欄の印は残っていない
    assert url.path == "/connect/receive/order/"
    assert not url.query and not url.fragment
    assert not BLANK_MARK_PATTERN.search(connection["受け口の URL"])

    # 取引先コードは A 社の partners.csv にあり、その行は外注先 (B 社)
    partner = partner_rows("a_machining")[connection["取引先コード"]]
    assert partner["取引先の種類"] == "外注先"
    assert partner["名前"] == load_answers("b_plating")["name"]


def test_b_plating_connects_from_a_machining_receive_only():
    connections = load_seed_yaml("b_plating", "connections.yaml")["つながり"]

    assert len(connections) == 1
    connection = connections[0]
    assert set(connection) == CONNECTION_KEYS
    assert connection["向き"] == "受ける"
    # 受ける向きの受け口の URL は空 (相手が送ってくる側)
    assert connection["受け口の URL"] == ""
    secret_env = SECRET_ENV_PATTERN.fullmatch(connection["鍵の置き場の名前"])
    assert secret_env is not None
    assert secret_env.group(1) == OTHER_COMPANY["b_plating"].upper()
    # 取引先コードは B 社の partners.csv にあり、その行は A 社 (得意先)
    partner = partner_rows("b_plating")[connection["取引先コード"]]
    assert partner["名前"] == load_answers("a_machining")["name"]
    assert partner["取引先の種類"] == "得意先"
