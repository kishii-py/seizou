"""命令 seizou new (src/seizou/cli.py) が業種の見本 (src/seizou/recipes/) を入れる試験。

置き場は一時のフォルダ (環境変数 SEIZOU_SITES_DIR)。main を直接呼び、置き場のデータベースは
manage.py shell で print した字で見る。試験の答えの YAML はこの試験の中で書く (作り物)。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from seizou import cli
from seizou.cli import main

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
PROCESS_ROWS = (
    "import json; from seizou.masters.models import Process; "
    "print(json.dumps([[p.code, p.name, p.use_in_plan] for p in Process.objects.order_by('sort_order', 'code')], ensure_ascii=False))"
)


# tests/test_cli_new.py の run_new・manage・write_answers と同じ形 (import せずに写す)
def run_new(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def manage(site: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "settings"}
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=site,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def write_answers(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def process_rows(site: Path) -> list[list]:
    """置き場の工程の (工程コード, 名前, 計画に使う) の並び (並び順の順)。"""
    result = manage(site, "shell", "--no-imports", "-c", PROCESS_ROWS)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def recipe_processes(recipe_id: str) -> list[list]:
    recipe = cli.load_recipes(cli.recipes_dir())[recipe_id]
    return [[p["code"], p["name"], p["use_in_plan"]] for p in recipe["processes"]]


def test_new_with_recipe_adds_fields_processes_settings(tmp_path, monkeypatch):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    answers = write_answers(tmp_path / "plate_co.yaml", "id: plate_co\nname: 見本のめっきの会社\nindustry: めっき\nport: 8013\n")
    code, out, err = run_new(["new", str(answers)])
    assert code == 0, err
    site = sites / "plate_co"

    models_text = (site / "fields" / "models.py").read_text(encoding="utf-8")
    assert "ext01 = models.CharField('処理の種類'" in models_text
    assert "ext02 = models.DecimalField('膜厚'" in models_text
    settings_text = (site / "settings.py").read_text(encoding="utf-8")
    assert "'over_shipment': 'warn'," in settings_text
    assert "'partial_shipment': True," in settings_text

    rows = process_rows(site)
    assert len(rows) == 3
    assert rows == recipe_processes("plating")

    assert "業種の見本 (表面処理) から入れました: 項目 2・工程 3・設定 2。" in out
    defaults = [line for line in out.splitlines() if line.startswith("答えに無い設定は既定を使いました")]
    assert defaults, out
    assert "over_shipment" not in defaults[0]
    assert "partial_shipment" not in defaults[0]
    # 見本の項目は 5 つの鍵を全部書くので、項目の属性の既定の行は出ない
    assert "答えに無い項目の属性は既定を使いました" not in out


def test_answer_fields_and_settings_win(tmp_path, monkeypatch):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    text = (
        "id: win_co\nname: 見本の表面処理の会社\nindustry: 表面処理\n"
        "item_fields:\n"
        "  - label: 処理の種類\n    type: choice\n    choices: [亜鉛, ニッケル]\n"
        "    required: false\n    in_list: false\n    searchable: false\n"
        "settings:\n  over_shipment: stop\n"
        "port: 8015\n"
    )
    answers = write_answers(tmp_path / "win_co.yaml", text)
    code, out, err = run_new(["new", str(answers)])
    assert code == 0, err
    site = sites / "win_co"

    models_text = (site / "fields" / "models.py").read_text(encoding="utf-8")
    assert re.search(r"ext01 = models\.CharField\('処理の種類', max_length=100, choices=", models_text)
    assert "ext02 = models.DecimalField('膜厚'" in models_text
    assert "ext03" not in models_text
    settings_text = (site / "settings.py").read_text(encoding="utf-8")
    assert "'over_shipment': 'stop'," in settings_text
    assert "'partial_shipment': True," in settings_text
    assert "業種の見本 (表面処理) から入れました: 項目 1・工程 3・設定 1。" in out


@pytest.mark.parametrize("extra", ["industry: 食品\n", "industry: めっき\nrecipe: none\n"])
def test_unmatched_or_none_adds_nothing(tmp_path, monkeypatch, extra):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    answers = write_answers(tmp_path / "plain_co.yaml", f"id: plain_co\nname: 見本の会社\n{extra}port: 8016\n")
    code, out, err = run_new(["new", str(answers)])
    assert code == 0, err
    site = sites / "plain_co"
    assert not (site / "fields").exists()
    assert process_rows(site) == []
    assert "業種の見本" not in out + err


def test_bad_recipe_value_is_refused(tmp_path, monkeypatch):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    answers = write_answers(tmp_path / "plain_co.yaml", "id: plain_co\nname: 見本の会社\nindustry: めっき\nrecipe: plating\nport: 8016\n")
    code, _out, err = run_new(["new", str(answers)])
    assert code == 2
    assert "recipe" in err
    assert not sites.exists()


def _plating() -> dict:
    return yaml.safe_load((cli.recipes_dir() / "plating.yaml").read_text(encoding="utf-8"))


def _long_code() -> dict:
    recipe = _plating()
    recipe["processes"][0]["code"] = "PRETREATME1"  # 11 字
    return recipe


def _unknown_root_key() -> dict:
    recipe = _plating()
    recipe["screens"] = []
    return recipe


def _same_alias() -> dict:
    recipe = _plating()
    recipe["industry"] = {"name": "鍍金工業", "aliases": ["鍍金工業", "メッキ"]}
    return recipe


@pytest.mark.parametrize(
    ("make", "word"),
    [(_long_code, "PRETREATME1"), (_unknown_root_key, "screens"), (_same_alias, "メッキ")],
)
def test_bad_recipe_file_is_refused(tmp_path, make, word):
    (tmp_path / "plating.yaml").write_text((cli.recipes_dir() / "plating.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "zz_broken.yaml").write_text(yaml.safe_dump(make(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(cli.AnswerError) as caught:
        cli.load_recipes(tmp_path)
    message = str(caught.value)
    assert "zz_broken.yaml" in message
    assert cli._industry_key(word) in message or word in message


def test_demo_seeds_win_over_recipe(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))

    code = cli.main(["new", str(COMPANIES_DIR / "b_plating.yaml"), "--demo"])

    out = capsys.readouterr().out
    assert code == 0, out
    seeds_line = "工程: 入れた 5 件・飛ばした 0 件"
    recipe_line = "業種の見本 (表面処理) と同じ物が全部あったので、足した物はありません。"
    assert seeds_line in out
    assert recipe_line in out
    assert out.index(seeds_line) < out.index(recipe_line)

    site = tmp_path / "b_plating"
    seeds = yaml.safe_load((COMPANIES_DIR / "b_plating" / "processes.yaml").read_text(encoding="utf-8"))["工程"]
    rows = process_rows(site)
    assert len(rows) == 5
    assert {row[0] for row in rows} == {seed["工程コード"] for seed in seeds}
    models_text = (site / "fields" / "models.py").read_text(encoding="utf-8")
    assert re.findall(r"^    (ext\d+) = ", models_text, re.M) == ["ext01", "ext02"]
