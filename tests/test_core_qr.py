"""QR コードの部品 (seizou.core.qr と template tag の seizou_qr)。DB は使わない。"""

import pytest
from django.template import Context, Template

from seizou.core.qr import qr_svg


def test_qr_svg_is_inline_svg():
    svg = qr_svg("https://example.invalid/workorders/1/scan/")

    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert 'viewBox="0 0 ' in svg
    assert "<path" in svg
    assert 'role="img"' in svg
    assert 'aria-label="QR コード"' in svg
    # 大きさは帳票の CSS で決める (根に width と height を残さない)
    assert 'width="' not in svg
    assert 'height="' not in svg
    assert "<?xml" not in svg


def test_qr_svg_is_stable():
    assert qr_svg("WO-0001") == qr_svg("WO-0001")
    assert qr_svg("WO-0001") != qr_svg("WO-0002")


@pytest.mark.parametrize("text", ["WO-<0001", "WO->0001", 'WO-"0001'])
def test_qr_svg_refuses_html_chars(text):
    with pytest.raises(ValueError, match="QR"):
        qr_svg(text)


def test_qr_tag_renders_svg_unescaped():
    html = Template("{% load seizou_qr %}{% qr_svg text %}").render(Context({"text": "WO-0001"}))

    assert html.startswith("<svg")
    assert "&lt;" not in html
