"""帳票の部品 (seizou.core.reports) の試験。

見本の納品書の context は scripts/render_sample_report.py の sample_context を使う (中身は作り物)。
印 pdf の試験は WeasyPrint と OS の pango が要る (Windows の CI では除く)。ほかは WeasyPrint が無くても通る。
"""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

import seizou.core
from seizou.core import reports

ROOT = Path(__file__).resolve().parent.parent


def load_sample_script():
    spec = importlib.util.spec_from_file_location(
        "render_sample_report", ROOT / "scripts" / "render_sample_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE = load_sample_script()


@pytest.fixture
def reports_without_weasyprint(monkeypatch):
    """weasyprint の import が失敗する状態で、seizou.core.reports を読み直した物を返す。"""
    for name in list(sys.modules):
        if name.startswith("weasyprint."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    # 読み直した module が試験の後に残らないよう、親の package の属性も元に戻す。
    monkeypatch.delitem(sys.modules, "seizou.core.reports", raising=False)
    monkeypatch.delattr(seizou.core, "reports", raising=False)
    return importlib.import_module("seizou.core.reports")


def test_render_html_returns_sample_text():
    context = SAMPLE.sample_context()
    html = reports.render_html(SAMPLE.TEMPLATE, context)

    assert "納品書" in html
    assert context["customer"]["name"] in html
    assert context["company"]["name"] in html
    assert context["company"]["bank_account"] in html
    for label in ("数量", "単価", "金額", "合計"):
        assert label in html
    for line in context["lines"]:
        assert line["item_name"] in html
    assert "116,150" in html
    assert 'href="/static/css/report.css"' in html


def test_reports_module_imports_without_weasyprint(reports_without_weasyprint):
    module = reports_without_weasyprint

    assert "weasyprint" not in module.__dict__
    assert "架空商事株式会社" in module.render_html(SAMPLE.TEMPLATE, SAMPLE.sample_context())


def test_render_pdf_raises_pdf_unavailable_without_weasyprint(reports_without_weasyprint):
    module = reports_without_weasyprint

    with pytest.raises(module.PdfUnavailable):
        module.render_pdf(SAMPLE.TEMPLATE, SAMPLE.sample_context())


@pytest.mark.pdf
def test_render_pdf_embeds_bundled_font():
    data = reports.render_pdf(SAMPLE.TEMPLATE, SAMPLE.sample_context())

    assert data.startswith(b"%PDF")
    # 同梱のフォントが読めないと、PDF の中のフォントの名前は手元のフォント (Hiragino など) になる。
    assert b"NotoSansJP" in data
    assert b"/FontFile2" in data


@pytest.mark.pdf
def test_render_pdf_sample_is_single_a4_page():
    document = reports._render_document(SAMPLE.TEMPLATE, SAMPLE.sample_context())

    assert len(document.pages) == 1
    page = document.pages[0]
    # A4 縦 = 210mm × 297mm (WeasyPrint の長さは CSS の px。1mm = 96 / 25.4 px)
    assert round(page.width * 25.4 / 96) == 210
    assert round(page.height * 25.4 / 96) == 297


def test_url_fetcher_allows_static_and_media_only(settings, tmp_path):
    media = tmp_path / "media"
    (media / "company").mkdir(parents=True)
    (media / "company" / "logo.png").write_bytes(b"\x89PNG sample")
    (tmp_path / "outside.txt").write_text("media の外", encoding="utf-8")
    settings.MEDIA_ROOT = media
    base = reports.BASE_URL

    body, content_type = reports.fetch_local(base + "static/css/report.css")
    assert b"@font-face" in body
    assert content_type == "text/css"

    body, _ = reports.fetch_local(base + "static/fonts/NotoSansJP-VF.ttf")
    assert body[:4] == b"\x00\x01\x00\x00"  # TrueType の印

    body, content_type = reports.fetch_local(base + "media/company/logo.png")
    assert body == b"\x89PNG sample"
    assert content_type == "image/png"

    css_path = ROOT / "src" / "seizou" / "core" / "static" / "css" / "report.css"
    refused = [
        "http://example.com/static/css/report.css",
        "https://example.com/static/css/report.css",
        "http://seizou.invalid/static/css/report.css",
        css_path.as_uri(),
        "file:///etc/hosts",
        "data:text/css;base64,Ym9keXt9",
        base + "pyproject.toml",
        base + "src/seizou/core/static/css/report.css",
        base + "static/../pyproject.toml",
        base + "static/%2e%2e/%2e%2e/pyproject.toml",
        base + "static//etc/hosts",
        base + "static/css\\..\\..\\models.py",
        base + "media/../outside.txt",
        base + "media/%2E%2E/outside.txt",
        base + "media//etc/hosts",
    ]
    for url in refused:
        with pytest.raises(ValueError):
            reports.fetch_local(url)

    with pytest.raises(FileNotFoundError):
        reports.fetch_local(base + "static/css/no-such-file.css")
