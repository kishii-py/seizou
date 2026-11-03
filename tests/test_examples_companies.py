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

    assert set(settings) == set(SETTING_VALUES) | BOOL_SETTINGS | {"hidden_fields", "access_control"}
    for key, allowed in SETTING_VALUES.items():
        assert settings[key] in allowed, key
    for key in BOOL_SETTINGS:
        assert isinstance(settings[key], bool), key
    assert settings["hidden_fields"] == []


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_access_control_is_on(company_id):
    # 2 社とも権限の判定を 3 つとも有効にする。YAML 1.1 は引用符の無い on を真偽で読むので、値が字であることも見る。
    access_control = load_answers(company_id)["settings"]["access_control"]

    assert access_control == {"department": "on", "write": "on", "money": "on"}
    assert all(isinstance(value, str) for value in access_control.values())


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

    assert [c["向き"] for c in connections] == ["送る", "受ける"]
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

    # 取引先コードは A 社の partners.csv にあり、その行は協力企業 (B 社)
    partner = partner_rows("a_machining")[connection["取引先コード"]]
    assert partner["取引先の種類"] == "協力企業"
    assert partner["名前"] == load_answers("b_plating")["name"]

    # 逆向き (B 社から注文回答と出荷の知らせを受ける)。取引先と鍵の置き場の名前は送る向きと同じ、受け口の URL は空
    back = connections[1]
    assert set(back) == CONNECTION_KEYS
    assert back["取引先コード"] == connection["取引先コード"]
    assert back["鍵の置き場の名前"] == connection["鍵の置き場の名前"]
    assert back["受け口の URL"] == ""


def test_b_plating_connects_with_a_machining_both_ways():
    connections = load_seed_yaml("b_plating", "connections.yaml")["つながり"]
    a_port = load_answers("a_machining")["port"]

    assert [c["向き"] for c in connections] == ["受ける", "送る"]
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

    # 逆向き (A 社へ注文回答と出荷の知らせを送る)。取引先と鍵の置き場の名前は受ける向きと同じ、受け口は A 社の connect の受け口
    back = connections[1]
    assert set(back) == CONNECTION_KEYS
    assert back["取引先コード"] == connection["取引先コード"]
    assert back["鍵の置き場の名前"] == connection["鍵の置き場の名前"]
    url = urlsplit(back["受け口の URL"])
    assert url.scheme == "http"
    assert url.port == a_port
    assert url.path == "/connect/receive/order/"
    assert not url.query and not url.fragment
    assert not BLANK_MARK_PATTERN.search(back["受け口の URL"])


# ---------------------------------------------------------------- 新しい種のファイル (標準の工程・指示書・出荷・納品書・請求書・図面)

MORE_SEED_YAML_ROOT_KEYS = {
    "workorders.yaml": "指示書",
    "shipments.yaml": "出荷",
    "delivery_notes.yaml": "納品書",
    "invoices.yaml": "請求書",
}
MORE_SEED_FILES = ("item_processes.csv", *MORE_SEED_YAML_ROOT_KEYS)
ITEM_PROCESS_HEADER = ["自社品番", "工程コード", "順"]
PLAN_WINDOW = ("2026-11-02", "2026-11-13")
DRAWING_MAX_BYTES = 50 * 1024


def company_dir(company_id):
    return COMPANIES_DIR / company_id


def orders_by_key(company_id):
    """(得意先の取引先コード, 得意先の注文番号, 行番号) → 明細の数量。"""
    found = {}
    for order in load_seed_yaml(company_id, "orders.yaml")["受注"]:
        for line_no, line in enumerate(order["明細"], start=1):
            found[(order["得意先の取引先コード"], order["得意先の注文番号"], line_no)] = line["数量"]
    # 届いた注文の写し (connect の種) も受注になる。出荷の種はこの受注も 取引先コード × 得意先の注文番号 × 行番号 で指す
    if (company_dir(company_id) / "acceptances.yaml").is_file():
        for entry in load_seed_yaml(company_id, "acceptances.yaml")["届いた注文"]:
            for line_no, line in enumerate(entry["明細"], start=1):
                found[(entry["取引先コード"], entry["相手の発注番号"], line_no)] = line["数量"]
    return found


def shipment_keys(company_id):
    """種の出荷の (得意先の取引先コード, 得意先の注文番号, 行番号, 出荷日)。"""
    return [
        (s["得意先の取引先コード"], s["得意先の注文番号"], s["行番号"], str(s["出荷日"]))
        for s in load_seed_yaml(company_id, "shipments.yaml")["出荷"]
    ]


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_more_seed_files_exist(company_id):
    for name in MORE_SEED_FILES:
        assert (company_dir(company_id) / name).is_file(), name
    for name, root in MORE_SEED_YAML_ROOT_KEYS.items():
        data = load_seed_yaml(company_id, name)
        assert set(data) == {root}, name
        assert isinstance(data[root], list) and data[root], name


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_item_processes_header_and_codes(company_id):
    path = company_dir(company_id) / "item_processes.csv"
    assert path.read_bytes().startswith(BOM)
    rows = read_csv_rows(path)
    assert rows[0] == ITEM_PROCESS_HEADER
    part_nos = {row[0] for row in read_csv_rows(company_dir(company_id) / "items.csv")[1:]}
    codes = {p["工程コード"] for p in load_seed_yaml(company_id, "processes.yaml")["工程"]}
    per_item = {}
    for part_no, code, seq in rows[1:]:
        assert part_no in part_nos, part_no
        assert code in codes, code
        per_item.setdefault(part_no, []).append(int(seq))
    # 30 品の全部に 2〜4 工程。順は 1 から重ならずに並ぶ
    assert set(per_item) == part_nos
    for part_no, seqs in per_item.items():
        assert 2 <= len(seqs) <= 4, part_no
        assert sorted(seqs) == list(range(1, len(seqs) + 1)), part_no


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_seed_references_exist(company_id):
    orders = orders_by_key(company_id)
    work_orders = load_seed_yaml(company_id, "workorders.yaml")["指示書"]
    shipments = shipment_keys(company_id)
    for entry in work_orders:
        assert (entry["得意先の取引先コード"], entry["得意先の注文番号"], entry["行番号"]) in orders
    for customer, order_no, line_no, _day in shipments:
        assert (customer, order_no, line_no) in orders
    for name, root in (("delivery_notes.yaml", "納品書"), ("invoices.yaml", "請求書")):
        for entry in load_seed_yaml(company_id, name)[root]:
            assert entry["出荷"], name
            for ref in entry["出荷"]:
                key = (entry["得意先の取引先コード"], ref["得意先の注文番号"], ref["行番号"], str(ref["出荷日"]))
                assert key in shipments, (name, key)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_seed_shipments_within_order_qty(company_id):
    orders = orders_by_key(company_id)
    shipped = {}
    for s in load_seed_yaml(company_id, "shipments.yaml")["出荷"]:
        key = (s["得意先の取引先コード"], s["得意先の注文番号"], s["行番号"])
        shipped[key] = shipped.get(key, 0) + s["数量"]
    for key, total in shipped.items():
        assert total <= orders[key], key
    ordered = {}
    for entry in load_seed_yaml(company_id, "workorders.yaml")["指示書"]:
        key = (entry["得意先の取引先コード"], entry["得意先の注文番号"], entry["行番号"])
        ordered[key] = ordered.get(key, 0) + entry["数量"]
        assert all(step["予定の数"] == entry["数量"] for step in entry["工程"])
    for key, total in ordered.items():
        assert total <= orders[key], key
    # 1 つの出荷は納品書 1 枚にだけ入る。同じ明細に同じ日の出荷は 2 つ無い
    keys = shipment_keys(company_id)
    assert len(keys) == len(set(keys))
    noted = [
        (n["得意先の取引先コード"], r["得意先の注文番号"], r["行番号"], str(r["出荷日"]))
        for n in load_seed_yaml(company_id, "delivery_notes.yaml")["納品書"]
        for r in n["出荷"]
    ]
    assert sorted(noted) == sorted(keys)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_seed_plan_dates_this_and_next_week(company_id):
    first, last = PLAN_WINDOW
    this_week = next_week = 0
    for entry in load_seed_yaml(company_id, "workorders.yaml")["指示書"]:
        for step in entry["工程"]:
            start, end = step.get("開始日"), step.get("終了日")
            assert (start is None) == (end is None)
            if start is None:
                continue
            assert first <= str(start) <= str(end) <= last, step
            assert start.weekday() < 5 and end.weekday() < 5
            if str(end) <= "2026-11-06":
                this_week += 1
            elif str(start) >= "2026-11-09":
                next_week += 1
    assert this_week and next_week


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_drawing_files_exist_and_are_small(company_id):
    Image = pytest.importorskip("PIL.Image", reason="図面を開く Pillow が入っていない")

    rows = read_csv_rows(company_dir(company_id) / "items.csv")
    column = rows[0].index("図面ファイル")
    names = [row[column] for row in rows[1:] if row[column]]
    if company_id == "b_plating":
        assert names == [] and not (company_dir(company_id) / "files").exists()
        return
    assert len(names) >= 3
    for name in names:
        assert "/" not in name and "\\" not in name and ".." not in name
        path = company_dir(company_id) / "files" / name
        assert path.is_file(), name
        assert path.stat().st_size <= DRAWING_MAX_BYTES, name
        with Image.open(path) as image:
            assert image.format == ("TIFF" if name.lower().endswith(".tif") else "PNG")
    assert any(name.lower().endswith(".tif") for name in names)
