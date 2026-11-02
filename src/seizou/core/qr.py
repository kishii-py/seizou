"""QR コードの部品 (現品票・出荷ラベル・納品書の QR)。

QR は qrcode の SvgPathImage で inline の SVG の字にして、帳票の HTML にそのまま入れる。
SVG にした理由: WeasyPrint の PDF とブラウザの印刷で同じ形になる。画像のファイルを作らないので
media に何も残らない。帳票の取り口 (core.reports.fetch_local) を通らずに描ける。
SVG の中は黒い四角の path だけで、QR の中身の字そのものは SVG に入らない。
大きさは SVG の根に持たせず、帳票の CSS (report.css の class) で決める。
"""

from __future__ import annotations

import qrcode
from qrcode.image.svg import SvgPathImage

#: QR の中身に使えない字 (SVG の字を HTML にそのまま入れるので、HTML の字は最初に断る)
_HTML_CHARS = ("<", ">", '"')


def qr_svg(text: str) -> str:
    """text を中身にした QR コードの SVG の字 (XML の宣言なし) を返す。ファイルに書かない。"""
    if any(char in text for char in _HTML_CHARS):
        raise ValueError(
            'QR の中身に HTML の字 (<, >, ") は使えません。URL か番号を渡してください。'
        )
    img = qrcode.make(text, image_factory=SvgPathImage, box_size=10, border=2)
    root = img.get_image()
    # 根の width と height (例 37mm) は字の長さで変わるので外し、viewBox だけを残す。
    root.attrib.pop("width", None)
    root.attrib.pop("height", None)
    root.set("role", "img")
    root.set("aria-label", "QR コード")
    return img.to_string(encoding="unicode")
