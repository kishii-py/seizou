"""業務マニュアルの本文 (Markdown の方言) を HTML に描く部品 (02 の 6 節)。方言の約束はこの module の 1 か所。

字から字への関数で、Django を使わない (settings なしで呼べる)。この module の中の `import markdown` は
site-packages の Python-Markdown を指す (Python 3 の import は絶対)。ほかの module からは
`from seizou.manuals import markdown as manual_markdown` の形で呼ぶ。

公開する物: render(body, *, images, manual_format) -> Rendered
  images: {画像の番号 (int): URL (str)}。本文の `![説明](image:<番号>)` を、この URL に替える。
  manual_format: "steps" (ステップ形式。## に 1・2・… の番号) か "page" (ページ形式。## に i・ii・… の番号)。
  Rendered.html は逃がしと許す形を済ませた HTML の字 (画面で印を付けるのは template tag の 1 か所)。
  Rendered.toc は ## の目次 (TocEntry の並び)。
公開する物: line_mark(line) -> LineMark | None  本文の 1 行の書式の記号と、その意味の目印の字 (差分の画面が行の頭に出す)。

方言:
- 生の HTML は字として出す (Python-Markdown の生の HTML の 2 つの口を外す)。
- 枠 `:::注意`・`:::危険`・`:::ヒント`・`:::こうする`・`:::してはいけない` … `:::` の行まで。
- ピル `{OK}`・`{NG}`・`{注意}`・`{情報}`・`{記録}`・`{要確認}`。
- 画像は `image:<番号>` だけ。リンクは http・https・mailto・#・/ で始まる物だけ (// で始まる物は外す)。
- 画像だけの段落の次の行の `> 説明` は写真カード。`- [ ]`・`- [x]` はチェック項目。
  `- 2026-11-03 [変更] 題 — 1 文` は経緯 (印は 変更・トラブル・メモ)。

属性を書ける拡張と、HTML の中に Markdown を書く拡張は入れない (属性と生の HTML を書けるようになるため)。
1 回ごとに新しい Markdown の物を作る (目次と状態を前の回から持ち越さない)。
class の字は下の辞書に固定の字で書き、_body.html の冒頭の comment にも同じ字を並べる (CSS の道具はテンプレートしか読まない)。
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as etree
from dataclasses import dataclass

import markdown
from markdown.blockprocessors import BlockProcessor
from markdown.extensions import Extension
from markdown.extensions.toc import TocExtension, render_inner_html, slugify_unicode, strip_tags
from markdown.inlinepatterns import InlineProcessor
from markdown.treeprocessors import Treeprocessor
from markdown.util import AtomicString

#: 枠の種類 → 枠の class
BOX_CLASSES = {
    "注意": "my-4 rounded-box border-l-4 border-warning bg-warning/10 p-4",
    "危険": "my-4 rounded-box border-l-4 border-error bg-error/10 p-4",
    "ヒント": "my-4 rounded-box border-l-4 border-info bg-info/10 p-4",
    "こうする": "my-4 rounded-box border-l-4 border-success bg-success/10 p-4",
    "してはいけない": "my-4 rounded-box border-l-4 border-error bg-base-200 p-4",
}

#: ピルの字 → class
PILL_CLASSES = {
    "OK": "badge badge-sm badge-success whitespace-nowrap",
    "NG": "badge badge-sm badge-error whitespace-nowrap",
    "注意": "badge badge-sm badge-warning whitespace-nowrap",
    "情報": "badge badge-sm badge-info whitespace-nowrap",
    "記録": "badge badge-sm badge-neutral whitespace-nowrap",
    "要確認": "badge badge-sm badge-outline badge-warning whitespace-nowrap",
}

#: 経緯の印 → class
EVENT_CLASSES = {
    "変更": "badge badge-sm badge-info",
    "トラブル": "badge badge-sm badge-error",
    "メモ": "badge badge-sm badge-neutral",
}

#: ## の番号の class (形式 → class)
NUMBER_CLASSES = {
    "steps": "mr-2 badge badge-primary",
    "page": "mr-2 text-base-content/50",
}

#: ほかの部品の class
CLASSES = {
    "box_title": "mb-2 font-bold",
    "event_item": "list-none",
    "check_item": "list-none",
    "check_box": "checkbox checkbox-sm mr-2 align-middle disabled:opacity-100 border-base-content/50",
    "figure": "my-4 overflow-hidden rounded-box border border-base-300",
    "figcaption": "p-2 text-xs text-base-content/50",
}

#: class の辞書の全部 (試験が _body.html の comment と突き合わせる)
CLASS_TABLES = (BOX_CLASSES, PILL_CLASSES, EVENT_CLASSES, NUMBER_CLASSES, CLASSES)

BOX_START_RE = re.compile(r"^:::(注意|危険|ヒント|こうする|してはいけない)[ \t]*$")
BOX_END = ":::"
PILL_RE = r"\{(OK|NG|注意|情報|記録|要確認)\}"
IMAGE_SRC_RE = re.compile(r"^image:(\d+)$")
CHECK_RE = re.compile(r"^\[( |x|X)\] ")
EVENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \[(変更|トラブル|メモ)\] (.+?) — (.+)$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
ALLOWED_LINK_PREFIXES = ("http://", "https://", "mailto:", "#", "/")
#: / で始まっても、ほかの場所 (別のサーバー) を指す形。ブラウザは \ を / と読む
OTHER_HOST_PREFIXES = ("//", "/\\")


@dataclass(frozen=True)
class TocEntry:
    """目次の 1 行。number は ## の番号の字 (1 か i)、title は見出しの字 (番号を除く)、anchor は id。"""

    number: str
    title: str
    anchor: str


@dataclass(frozen=True)
class LineMark:
    """本文の 1 行の書式の記号。kind は種類、label は目印の字、symbol は記号の部分、rest は残りの字。

    symbol と rest をつなぐと、元の行と同じ字になる (差分の画面は行の字を変えずに並べるため)。
    """

    kind: str
    label: str
    symbol: str
    rest: str


def line_mark(line: str) -> LineMark | None:
    """本文の 1 行の書式の記号と、その意味の目印の字。書式の記号の無い行は None。

    差分の画面は本文の字の行のまま並べるので、`##`・`- [ ]`・`:::注意` の意味を行の頭の目印で読めるようにする。
    見分け方は描く所と同じ正規表現 (BOX_START_RE・BOX_END・CHECK_RE・EVENT_RE) を使う。
    目印を付けるのは ## の見出し・チェック項目・枠の始まりと終わり・経緯・ほかの `- ` の箇条書きだけ
    (地の文・### の小見出し・番号の付いた並び・画像の行・写真カードの `> `・空の行には付けない)。
    """
    if line.startswith("## "):
        return LineMark("heading", "見出し", "##", line[2:])
    box = BOX_START_RE.match(line)
    if box is not None:
        return LineMark("box_start", f"{box.group(1)}の枠の始まり", line, "")
    if line.strip() == BOX_END:
        return LineMark("box_end", "枠の終わり", line, "")
    if line.startswith("- "):
        item = line[2:]
        if CHECK_RE.match(item):
            return LineMark("check", "チェック項目", line[:5], line[5:])
        if EVENT_RE.match(item.strip()):
            return LineMark("event", "経緯", "-", line[1:])
        return LineMark("bullet", "箇条書き", "-", line[1:])
    return None


@dataclass(frozen=True)
class Rendered:
    """描いた本文。html は逃がしと許す形を済ませた字、toc は ## の目次。"""

    html: str
    toc: tuple[TocEntry, ...] = ()


def roman(n: int) -> str:
    """小文字のローマ数字 (1 → i、4 → iv、9 → ix)。"""
    pairs = (
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    )
    out = []
    for value, letters in pairs:
        while n >= value:
            out.append(letters)
            n -= value
    return "".join(out)


def is_allowed_link(href: str) -> bool:
    """リンクの href が許す形か。字の参照を戻し、制御文字と前後の空白を除き、小文字にして見る。"""
    value = CONTROL_RE.sub("", html.unescape(href or "")).strip().lower()
    if value.startswith(OTHER_HOST_PREFIXES):
        return False
    return value.startswith(ALLOWED_LINK_PREFIXES)


def _find_box_end(pieces: list[str]) -> tuple[int, int] | None:
    """枠の閉じの行 (`:::` だけの行) の場所 (何番目の block の何行目)。無ければ None。"""
    for index, piece in enumerate(pieces):
        for line_no, line in enumerate(piece.split("\n")):
            if line.strip() == BOX_END:
                return index, line_no
    return None


class BoxProcessor(BlockProcessor):
    """枠 `:::<種類>` … `:::`。閉じの行が無い・知らない種類は枠にしない (字のまま出る)。"""

    def test(self, parent, block):
        return BOX_START_RE.match(block.split("\n", 1)[0]) is not None

    def run(self, parent, blocks):
        first, _sep, rest = blocks[0].partition("\n")
        kind = BOX_START_RE.match(first).group(1)
        pieces = [rest, *blocks[1:]]
        found = _find_box_end(pieces)
        if found is None:
            return False
        index, line_no = found
        lines = pieces[index].split("\n")
        inner = [*pieces[:index], "\n".join(lines[:line_no])]
        after = "\n".join(lines[line_no + 1 :])
        del blocks[: index + 1]
        if after.strip():
            blocks.insert(0, after)
        box = etree.SubElement(parent, "div", {"class": BOX_CLASSES[kind]})
        title = etree.SubElement(box, "p", {"class": CLASSES["box_title"]})
        title.text = AtomicString(kind)
        self.parser.parseBlocks(box, [piece for piece in inner if piece.strip()])
        return True


class PillProcessor(InlineProcessor):
    """ピル `{OK}` など。コードの中は効かない (コードの方が先に読まれる)。"""

    def handleMatch(self, m, data):
        word = m.group(1)
        el = etree.Element("span", {"class": PILL_CLASSES[word]})
        el.text = AtomicString(word)
        return el, m.start(0), m.end(0)


def _only_image(el) -> bool:
    """画像だけの段落か。"""
    return (
        el.tag == "p"
        and not (el.text or "").strip()
        and len(el) == 1
        and el[0].tag == "img"
        and not (el[0].tail or "").strip()
    )


def _text_holder(li):
    """li の字を持つ要素。詰めた箇条は li、間を空けた箇条 (中に段落) は先頭の p。無ければ None。"""
    if (li.text or "").strip():
        return li
    if len(li) and li[0].tag == "p" and (li[0].text or "").strip():
        return li[0]
    return None


class ManualTreeprocessor(Treeprocessor):
    """画像の番号・リンクの許す形・写真カード・チェック項目・経緯・## の番号と目次。

    inline (20) の後 (link と画像の要素ができてから) で、toc (5) の前に回る。
    """

    def __init__(self, md, images: dict, manual_format: str):
        super().__init__(md)
        self.images = images
        self.manual_format = manual_format
        self.toc: list[TocEntry] = []

    def run(self, root):
        self.photo_cards(root)
        self.images_by_number(root)
        self.allowed_links(root)
        self.list_items(root)
        self.number_headings(root)

    def photo_cards(self, root) -> None:
        """画像だけの p のすぐ次が blockquote なら、2 つを figure (画像と figcaption) に替える。"""
        for parent in list(root.iter()):
            children = list(parent)
            index = 0
            while index < len(children) - 1:
                para, quote = children[index], children[index + 1]
                if not (_only_image(para) and quote.tag == "blockquote"):
                    index += 1
                    continue
                at = list(parent).index(para)
                figure = etree.Element("figure", {"class": CLASSES["figure"]})
                img = para[0]
                img.tail = None
                figure.append(img)
                caption = etree.SubElement(figure, "figcaption", {"class": CLASSES["figcaption"]})
                source = quote[0] if len(quote) == 1 and quote[0].tag == "p" else quote
                caption.text = source.text
                for child in list(source):
                    caption.append(child)
                figure.tail = quote.tail
                parent.remove(para)
                parent.remove(quote)
                parent.insert(at, figure)
                index += 2

    def images_by_number(self, root) -> None:
        """image:<番号> の画像を口の URL に替える。ほか (外の画像・知らない番号) は「[画像: 説明]」の字にする。"""
        for img in list(root.iter("img")):
            match = IMAGE_SRC_RE.match((img.get("src") or "").strip())
            url = self.images.get(int(match.group(1))) if match else None
            if url:
                img.set("src", url)
                img.set("loading", "lazy")
                continue
            alt = img.get("alt") or ""
            tail = img.tail
            img.clear()
            img.tag = "span"
            img.text = f"[画像: {alt}]"
            img.tail = tail

    def allowed_links(self, root) -> None:
        """許す形でないリンクは span にして属性を全部外す (字は残す)。"""
        for link in list(root.iter("a")):
            if not is_allowed_link(link.get("href", "")):
                link.tag = "span"
                link.attrib.clear()

    def list_items(self, root) -> None:
        for li in list(root.iter("li")):
            holder = _text_holder(li)
            if holder is None:
                continue
            check = CHECK_RE.match(holder.text)
            if check:
                box = etree.Element("input", {"type": "checkbox", "class": CLASSES["check_box"], "disabled": "disabled"})
                if check.group(1) in "xX":
                    box.set("checked", "checked")
                box.tail = holder.text[check.end() :]
                holder.text = None
                holder.insert(0, box)
                li.set("class", CLASSES["check_item"])
                continue
            if len(holder):
                continue
            event = EVENT_RE.match(holder.text.strip())
            if event:
                self.event_item(holder, *event.groups())
                li.set("class", CLASSES["event_item"])

    @staticmethod
    def event_item(holder, day: str, kind: str, title: str, sentence: str) -> None:
        """経緯の 1 行 (日付・印・題・1 文)。"""
        holder.text = None
        time = etree.SubElement(holder, "time", {"datetime": day})
        time.text = day
        time.tail = " "
        mark = etree.SubElement(holder, "span", {"class": EVENT_CLASSES[kind]})
        mark.text = kind
        mark.tail = " "
        strong = etree.SubElement(holder, "strong")
        strong.text = title
        strong.tail = " "
        line = etree.SubElement(holder, "span")
        line.text = sentence

    def number_headings(self, root) -> None:
        """根の直下の h2 に順に番号と id を付け、目次を集める (枠の中の ## と # と ### は入れない)。"""
        page = self.manual_format == "page"
        number_class = NUMBER_CLASSES["page"] if page else NUMBER_CLASSES["steps"]
        headings = [child for child in root if child.tag == "h2"]
        for n, heading in enumerate(headings, start=1):
            number = roman(n) if page else str(n)
            anchor = f"section-{n}" if page else f"step-{n}"
            title = html.unescape(strip_tags(render_inner_html(heading, self.md)))
            mark = etree.Element("span", {"class": number_class})
            mark.text = number
            mark.tail = heading.text
            heading.text = None
            heading.insert(0, mark)
            heading.set("id", anchor)
            self.toc.append(TocEntry(number=number, title=title, anchor=anchor))


class ManualExtension(Extension):
    """方言の 5 つの仕組みを登録する拡張。"""

    def __init__(self, *, images: dict, manual_format: str):
        super().__init__()
        self.images = images
        self.manual_format = manual_format
        self.tree: ManualTreeprocessor | None = None

    def extendMarkdown(self, md):
        # 生の HTML は字にする (出力の時に Python-Markdown が < と > を逃がす)。
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        # 枠は組み込みのどれより先 (中の ##・表・---・> に割られないため)。
        md.parser.blockprocessors.register(BoxProcessor(md.parser), "manual_box", 105)
        md.inlinePatterns.register(PillProcessor(PILL_RE, md), "manual_pill", 70)
        self.tree = ManualTreeprocessor(md, self.images, self.manual_format)
        md.treeprocessors.register(self.tree, "manual_tree", 15)


def render(body: str, *, images: dict, manual_format: str) -> Rendered:
    """本文の Markdown を HTML と目次に描く。"""
    extension = ManualExtension(images=dict(images or {}), manual_format=manual_format)
    md = markdown.Markdown(
        extensions=[
            "tables",
            "sane_lists",
            TocExtension(slugify=slugify_unicode, marker=""),
            extension,
        ],
        output_format="html",
    )
    text = md.convert(body or "")
    toc = tuple(extension.tree.toc) if extension.tree is not None else ()
    return Rendered(html=text, toc=toc)
