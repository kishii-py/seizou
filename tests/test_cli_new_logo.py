"""seizou new が答えの YAML の logo (任意) から、会社のロゴと印 (favicon) を入れる試験 (決め 11-05 20:1x。N166)。

置き場は一時のフォルダ (環境変数 SEIZOU_SITES_DIR)。答えは tests/fixtures/companies/sample.yaml (作り物) と、
それに logo の 1 行を足した写し。ロゴは試験が書く作り物の PNG。
"""

from __future__ import annotations

from PIL import Image

from tests.test_cli_new import FIXTURE, manage, run_new

ROW = "from seizou.core.models import Company; print(Company.objects.get().logo.name)"


def test_new_with_logo_puts_logo_and_icons(tmp_path, monkeypatch):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    answers = tmp_path / "answers.yaml"
    answers.write_text(FIXTURE.read_text(encoding="utf-8") + "logo: art/ロゴ.png\n", encoding="utf-8")

    code, _out, err = run_new(["new", str(answers)])  # ファイルが無ければ、何も書かずに止まる
    assert code == 2 and "logo" in err
    assert not sites.exists()

    (tmp_path / "art").mkdir()
    Image.new("RGB", (60, 30), (30, 120, 200)).save(tmp_path / "art" / "ロゴ.png")
    code, out, err = run_new(["new", str(answers)])
    assert code == 0, err
    assert "ロゴと印 (favicon) も入れました" in out
    site = sites / "sample_co"
    result = manage(site, "shell", "--no-imports", "-c", ROW)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "company/logo.png"
    for name, side in (("favicon.png", 32), ("apple-touch-icon.png", 180)):
        with Image.open(site / "media" / "company" / name) as icon:
            assert icon.size == (side, side), name


def test_new_without_logo_puts_nothing(tmp_path, monkeypatch):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    code, out, err = run_new(["new", str(FIXTURE)])
    assert code == 0, err
    assert "ロゴと印" not in out
    site = sites / "sample_co"
    result = manage(site, "shell", "--no-imports", "-c", ROW)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""  # ロゴの欄は空
    assert not (site / "media" / "company").exists()
