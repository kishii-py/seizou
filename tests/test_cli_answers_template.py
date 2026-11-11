"""命令 seizou answers-template (答えの YAML の雛形を書き出す口。依頼書 N426。精査 Q1 の足りない物の 1) の試験。

見る物:

- (d) cli.main(["answers-template", <道>]) が 0 を返してファイルを書き、cli.load_answers がそのファイルを誤りにせず、鍵が id・name・port の 3 つ。
- (e) 書いた字に industry・recipe・company・logo・item_fields・settings と docs/answers.md の字がある (注の中)。
- (e2) 任意の鍵の例 (logo を除く) の行の頭の「# 」を外した字を一時のファイルに書くと、cli.load_answers が誤りにしない
  (注を外せば通る約束)。例の行の決め方 = 行の頭が「# 」(# が 1 つと空白) の行が例で、「## 」で始まる行は説明 (雛形の頭の注の約束)。
  logo の例は、外すと絵のファイルが要るので外さない (雛形の注に書いてある)。外した後の答えに、例の鍵が全部あることも見る
  (例の行の決め方が 1 行も当たらずに通る形を作らない)。
- (f) 同じ道でもう 1 度打つと 2 を返し、ファイルの中身が変わらない。無いフォルダの下の道でも 2 を返し、何も作らない。
- (g) ~/ で始まる道 (HOME と USERPROFILE を一時のフォルダに差し替える。tests/test_cli_windows.py の (i) と同じ形) でもホームの下に書く。

道は一時のフォルダ。値は作り物。
"""

from __future__ import annotations

from pathlib import Path

from seizou import cli
from tests.test_cli_new import run_new

#: 雛形の中の任意の鍵の例 (logo は、外すとファイルが要るので e2 では外さない)
EXAMPLE_KEYS = ("industry", "recipe", "company", "item_fields", "settings")
DOC_WORDS = ("industry", "recipe", "company", "logo", "item_fields", "settings", "docs/answers.md")
EXAMPLE_HEAD = "# "


def _write(path: Path) -> tuple[int, str, str]:
    return run_new(["answers-template", str(path)])


def _uncomment_examples(text: str) -> str:
    """例の行 (頭が「# 」で、logo の行を除く) の「# 」を外した字。説明の行 (「## 」) と logo の例はそのまま。"""
    lines = []
    for line in text.splitlines():
        if line.startswith(EXAMPLE_HEAD) and not line[len(EXAMPLE_HEAD) :].startswith("logo:"):
            line = line[len(EXAMPLE_HEAD) :]
        lines.append(line)
    return "\n".join(lines) + "\n"


def test_writes_minimal_answers(tmp_path):
    # (d)
    target = tmp_path / "mihon.yaml"
    code, out, err = _write(target)
    assert code == 0, err
    assert target.is_file()
    answers = cli.load_answers(target)
    assert set(answers) == {"id", "name", "port"}
    assert f"書いた: {target}" in out
    assert f"seizou new {target}" in out


def test_template_names_optional_keys_in_comments(tmp_path):
    # (e)
    target = tmp_path / "mihon.yaml"
    assert _write(target)[0] == 0
    text = target.read_text(encoding="utf-8")
    missing = [word for word in DOC_WORDS if word not in text]
    assert not missing, missing
    comments = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    assert all(word in comments for word in DOC_WORDS)


def test_uncommented_examples_load(tmp_path):
    # (e2)
    target = tmp_path / "mihon.yaml"
    assert _write(target)[0] == 0
    opened = tmp_path / "opened.yaml"
    opened.write_text(_uncomment_examples(target.read_text(encoding="utf-8")), encoding="utf-8")
    answers = cli.load_answers(opened)
    missing = [key for key in EXAMPLE_KEYS if key not in answers]
    assert not missing, f"注を外した答えに、例の鍵が無い: {missing}"
    assert "logo" not in answers
    assert set(answers["company"]) == set(cli.COMPANY_KEYS)
    assert len(answers["item_fields"]) == 1


def test_does_not_overwrite_or_make_folders(tmp_path):
    # (f)
    target = tmp_path / "mihon.yaml"
    assert _write(target)[0] == 0
    before = target.read_bytes()
    code, out, err = _write(target)
    assert code == cli.EXIT_INPUT
    assert f"止まりました: 既にあります: {target} (上書きしません)" in err
    assert "書いた:" not in out
    assert target.read_bytes() == before

    missing = tmp_path / "no_such" / "mihon.yaml"
    code, out, err = _write(missing)
    assert code == cli.EXIT_INPUT
    assert f"止まりました: フォルダがありません: {missing.parent}" in err
    assert not missing.parent.exists()


def test_home_path_is_expanded(tmp_path, monkeypatch):
    # (g) POSIX は HOME、Windows は USERPROFILE からホームを読むので、2 つとも一時のフォルダにする
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    code, _out, err = run_new(["answers-template", "~/mihon.yaml"])
    assert code == 0, err
    assert (home / "mihon.yaml").is_file()
    assert not (Path("~") / "mihon.yaml").exists()
