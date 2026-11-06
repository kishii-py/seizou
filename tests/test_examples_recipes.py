"""業種の見本 (src/seizou/recipes/<業種の id>.yaml) の形と、例の 2 社の答えと種とそろうことを確かめる。

DB と置き場は使わない。見るのは、見本の鍵と値の約束・業種の名前と言い換えが見本どうしで重ならないこと・
機械加工の見本が A 社と、表面処理の見本が B 社と同じ中身であること (だから 2 社の seizou new で増える物が無い)。
"""

import re
from pathlib import Path

import pytest
import yaml

from seizou import cli

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
EXPECTED_IDS = ("casting", "machining", "plating", "sheet_metal")

ROOT_KEYS = {"industry", "item_fields", "processes", "settings"}
INDUSTRY_KEYS = {"name", "aliases"}
FIELD_KEYS = {"label", "type", "required", "in_list", "searchable"}
FIELD_FLAGS = ("required", "in_list", "searchable")
CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,9}")

# tests/test_examples_companies.py の SETTING_VALUES と BOOL_SETTINGS と同じ表 (import せずに写す)
SETTING_VALUES = {
    "order_form": {"multi", "single"},
    "over_shipment": {"warn", "stop", "off"},
    "over_workorder": {"warn", "stop", "off"},
    "receiving_required": {"warn", "stop", "off"},
    "order_number": {"seq", "ym_seq"},
}
BOOL_SETTINGS = {"partial_shipment", "production_plan"}

# 種の processes.yaml の日本語の鍵と、見本の工程の英語の鍵の対応
SEED_PROCESS_KEYS = {
    "工程コード": "code",
    "名前": "name",
    "協力企業に出すか": "is_outsourced",
    "並び順": "sort_order",
    "計画に使う": "use_in_plan",
}


def read_recipe(recipe_id):
    return yaml.safe_load((cli.recipes_dir() / f"{recipe_id}.yaml").read_text(encoding="utf-8"))


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_recipe_files_are_the_expected_ones():
    folder = cli.recipes_dir()
    assert tuple(sorted(path.stem for path in folder.glob("*.yaml"))) == EXPECTED_IDS
    assert tuple(cli.load_recipes(folder)) == EXPECTED_IDS
    # 見本は import しない (cli.py が Path で読む)。seizou.recipes という module を作らない
    assert not list(folder.glob("*.py"))


@pytest.mark.parametrize("recipe_id", EXPECTED_IDS)
def test_recipe_shape(recipe_id):
    recipe = read_recipe(recipe_id)
    assert set(recipe) == ROOT_KEYS

    industry = recipe["industry"]
    assert set(industry) == INDUSTRY_KEYS
    assert isinstance(industry["name"], str) and industry["name"]
    assert industry["name"] in industry["aliases"]

    fields = recipe["item_fields"]
    assert fields
    labels = [field["label"] for field in fields]
    assert len(labels) == len(set(labels)), labels
    for field in fields:
        expected = FIELD_KEYS | ({"choices"} if field["type"] == "choice" else set())
        assert set(field) == expected, field
        assert field["type"] in cli.FIELD_TYPES, field
        for flag in FIELD_FLAGS:
            assert isinstance(field[flag], bool), (field["label"], flag)

    processes = recipe["processes"]
    assert len(processes) >= 2
    for process in processes:
        assert set(process) == set(cli.RECIPE_PROCESS_KEYS), process
        assert CODE_PATTERN.fullmatch(process["code"]), process["code"]
        assert isinstance(process["is_outsourced"], bool)
        assert isinstance(process["use_in_plan"], bool)
        assert isinstance(process["sort_order"], int) and not isinstance(process["sort_order"], bool)
    codes = [process["code"] for process in processes]
    names = [process["name"] for process in processes]
    orders = [process["sort_order"] for process in processes]
    assert len(codes) == len(set(codes)), codes
    assert len(names) == len(set(names)), names
    assert orders == sorted(orders) and len(orders) == len(set(orders)), orders
    assert any(process["use_in_plan"] for process in processes)

    for key, value in recipe["settings"].items():
        assert key in cli.RECIPE_SETTING_KEYS, key
        if key in BOOL_SETTINGS:
            assert isinstance(value, bool), (key, value)
        else:
            assert value in SETTING_VALUES[key], (key, value)


def test_industry_words_do_not_overlap():
    recipes = cli.load_recipes(cli.recipes_dir())
    seen = {}
    for recipe_id, recipe in recipes.items():
        words = [recipe["industry"]["name"], *recipe["industry"]["aliases"]]
        for word in {cli._industry_key(word) for word in words}:
            assert word not in seen, (word, seen.get(word), recipe_id)
            seen[word] = recipe_id

    def matched(industry):
        found = cli.find_recipe({"industry": industry}, recipes)
        return found[0] if found else None

    assert matched("機械加工") == "machining"
    assert matched("表面処理") == "plating"
    assert matched("めっき") == "plating"
    assert matched("メッキ") == "plating"
    assert matched(" 鋳 物 ") == "casting"
    assert matched("板金加工") == "sheet_metal"
    assert matched("食品") is None
    assert matched("金属加工") is None


@pytest.mark.parametrize(("company_id", "recipe_id"), [("a_machining", "machining"), ("b_plating", "plating")])
def test_recipes_match_the_two_companies(company_id, recipe_id):
    answers = read_yaml(COMPANIES_DIR / f"{company_id}.yaml")
    recipes = cli.load_recipes(cli.recipes_dir())
    found = cli.find_recipe(answers, recipes)
    assert found and found[0] == recipe_id
    recipe = recipes[recipe_id]

    assert recipe["item_fields"] == answers["item_fields"]
    for key, value in recipe["settings"].items():
        assert answers["settings"][key] == value, key

    seeds = read_yaml(COMPANIES_DIR / company_id / "processes.yaml")["工程"]
    seed_by_code = {row["工程コード"]: {SEED_PROCESS_KEYS[key]: value for key, value in row.items()} for row in seeds}
    for process in recipe["processes"]:
        assert seed_by_code.get(process["code"]) == process, process["code"]

    _merged, added = cli.apply_recipe(answers, recipe)
    assert (added["項目"], added["設定"]) == (0, 0)
    assert added["工程"] == recipe["processes"]


def test_recipes_readme_lists_every_recipe():
    text = (cli.recipes_dir() / "README.md").read_text(encoding="utf-8")
    for recipe_id in EXPECTED_IDS:
        assert f"`{recipe_id}`" in text, recipe_id
        assert read_recipe(recipe_id)["industry"]["name"] in text, recipe_id
