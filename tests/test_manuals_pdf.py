"""業務マニュアルの PDF (A4 の帯とページ番号)。本物の WeasyPrint は呼ばない (render_pdf を差し替える)。"""

import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from seizou.core import reports
from seizou.manuals import markdown as manual_markdown
from seizou.manuals import services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def manual(user):
    folder = services.create_folder(None, "段取り", "blue", user)
    manual = services.create_manual(folder, "段取り替えの手順", "steps", user)
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), (30, 120, 200)).save(buffer, format="PNG")
    image = services.add_image(manual, SimpleUploadedFile("写真.png", buffer.getvalue()), user)
    return services.update_manual(manual, {"body": f"## 準備\n\n![写真](image:{image.pk})\n\n## 片付け"}, user)


def test_pdf_requires_login(client, manual):
    response = client.get(reverse("manuals:manual_pdf", args=[manual.pk]))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))


def test_pdf_returns_pdf_when_available(client_logged_in, manual, monkeypatch):
    seen = {}

    def fake_render_pdf(template, context):
        seen["template"] = template
        seen["context"] = context
        return b"%PDF-1.4 test"

    monkeypatch.setattr(reports, "render_pdf", fake_render_pdf)
    response = client_logged_in.get(reverse("manuals:manual_pdf", args=[manual.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'inline; filename="manual-{manual.pk}-v1.pdf"'
    assert "no-store" in response["Cache-Control"]
    assert response.content == b"%PDF-1.4 test"
    assert seen["template"] == "manuals/manual_print.html"
    assert seen["context"]["pdf"] is True
    assert [f.name for f in seen["context"]["path"]] == ["段取り"]


def test_pdf_falls_back_to_html_when_unavailable(client_logged_in, manual, monkeypatch):
    def unavailable(template, context):
        raise reports.PdfUnavailable("PDF の部品が使えません。")

    monkeypatch.setattr(reports, "render_pdf", unavailable)
    response = client_logged_in.get(reverse("manuals:manual_pdf", args=[manual.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "PDF の準備がありません" in html
    assert "段取り替えの手順" in html
    assert "版 1" in html
    assert "作った日時 " + timezone.localtime(manual.created_at).strftime("%Y-%m-%d %H:%M") in html
    assert "フォルダ 段取り" in html
    image = manual.images.get()
    assert f'src="{reverse("manuals:manualimage_download", args=[image.pk])}"' in html
    assert "/media/" not in html
    assert "目次" in html


FIVE_BOXES = """## 準備

:::注意
主軸が止まってから扉を開けます。
:::

:::危険
チャックの鍵を差したまま主軸を回しません。
:::

:::ヒント
爪の締め付けの力は段取り表の値に合わせます。
:::

:::こうする
1 個目は送りを落として削ります。
:::

:::してはいけない
測る前に 2 個目を削り始めません。
:::
"""


def _rules(html, selector):
    """印刷用の style の、selector で始まる規則の中身 ({ … } の字) の並び。"""
    return re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", html)


def test_print_boxes_differ_by_kind(user, manual):
    """業務マニュアルの紙で、5 つの枠 (注意・危険・ヒント・こうする・してはいけない) の形が種類ごとに違う (指示書 R9 = print-08)。

    紙の style は種類ごとの規則を持ち (画面の class の border-* と bg-base-200 で選ぶ)、本文の枠の div にその class が付く。
    危険 と してはいけない は同じ border-error なので、してはいけない だけ地の色の規則を持つ。
    """
    manual = services.update_manual(manual, {"body": FIVE_BOXES}, user)
    html = reports.render_html("manuals/manual_print.html", {"manual": manual, "path": [], "pdf": True})
    style = html.split("</style>", 1)[0]
    body = html.split("</style>", 1)[1]
    for box_class in manual_markdown.BOX_CLASSES.values():
        assert f'<div class="{box_class}">' in body
    kinds = {
        "危険": ".manual-body div.rounded-box.border-error {",
        "してはいけない": '.manual-body div.rounded-box.border-error[class~="bg-base-200"] {',
        "注意": ".manual-body div.rounded-box.border-warning {",
        "ヒント": ".manual-body div.rounded-box.border-info {",
        "こうする": ".manual-body div.rounded-box.border-success {",
    }
    bodies = {}
    for kind, selector in kinds.items():
        rules = _rules(style, selector.rstrip(" {"))
        assert rules, kind
        bodies[kind] = " ".join(rule.strip() for rule in rules)
    assert len(set(bodies.values())) == len(bodies), bodies
    assert "background" in bodies["してはいけない"]
    assert "double" in bodies["注意"] and "dotted" in bodies["ヒント"]
    # 危険 (と してはいけない) の題は黒地に白字
    title_rules = _rules(style, ".manual-body div.rounded-box.border-error > p.font-bold:first-child")
    assert title_rules and "background: #000" in title_rules[0] and "color: #fff" in title_rules[0]


def test_print_html_has_page_counter(manual):
    html = reports.render_html("manuals/manual_print.html", {"manual": manual, "path": [], "pdf": True})
    assert "counter(page)" in html and "counter(pages)" in html
    image = manual.images.get()
    assert f'src="/media/{image.file.name}"' in html
    assert "フォルダ トップ" in html
    assert "csrf" not in html.lower()
