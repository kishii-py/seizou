"""業務マニュアルの Markdown の方言 (manuals.markdown の render だけを呼ぶ。DB を使わない)。"""

from html.parser import HTMLParser
from pathlib import Path

import pytest

from seizou.manuals import markdown as manual_markdown
from seizou.manuals.markdown import TocEntry, render, roman

BODY_TEMPLATE = Path(manual_markdown.__file__).parent / "templates" / "manuals" / "_body.html"


class TagCollector(HTMLParser):
    """描いた HTML の要素と属性の並び (属性の値は字の参照を戻した物)。"""

    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags_of(text: str) -> list[tuple[str, dict]]:
    parser = TagCollector()
    parser.feed(text)
    parser.close()
    return parser.tags


def html_of(body: str, *, images=None, manual_format: str = "steps") -> str:
    return render(body, images=images or {}, manual_format=manual_format).html


def test_basic_blocks():
    body = (
        "# 題\n\n## 手順\n\n### 小見出し\n\n段落の字\n\n- 箇条 1\n- 箇条 2\n\n1. 一つ目\n2. 二つ目\n\n"
        "| 項目 | 値 |\n|---|---|\n| 寸法 | 10 |\n\n> 引用の字\n\n---\n"
    )
    text = html_of(body)
    names = [tag for tag, _attrs in tags_of(text)]
    for tag in ("h1", "h2", "h3", "p", "ul", "ol", "li", "table", "th", "td", "blockquote", "hr"):
        assert tag in names, tag
    assert "<p>段落の字</p>" in text
    assert "<td>寸法</td>" in text


@pytest.mark.parametrize("kind", list(manual_markdown.BOX_CLASSES))
def test_boxes(kind):
    text = html_of(f":::{kind}\n## 中の見出し\n\n- 中の箇条\n\n| 項目 | 値 |\n|---|---|\n| 寸法 | 10 |\n:::\n\n枠の外の段落")
    assert f'<div class="{manual_markdown.BOX_CLASSES[kind]}">' in text
    assert f'<p class="mb-2 font-bold">{kind}</p>' in text
    assert "<li>中の箇条</li>" in text
    assert "中の見出し</h2>" in text
    assert "<td>寸法</td>" in text
    assert "<p>枠の外の段落</p>" in text
    assert ":::" not in text


@pytest.mark.parametrize(
    ("body", "mark"),
    [(":::注意\n閉じの無い枠の字", ":::注意"), (":::foo\n知らない種類の字\n:::", ":::foo")],
)
def test_boxes_without_end_or_unknown_kind_stay_text(body, mark):
    text = html_of(body)
    assert "rounded-box" not in text
    assert mark in text


@pytest.mark.parametrize("word", list(manual_markdown.PILL_CLASSES))
def test_pills(word):
    css = manual_markdown.PILL_CLASSES[word]
    text = html_of(f"結果は {{{word}}} です。\n\n| 項目 | 状態 |\n|---|---|\n| 寸法 | {{{word}}} |")
    assert text.count(f'<span class="{css}">{word}</span>') == 2
    assert f'<td><span class="{css}">{word}</span></td>' in text


def test_pills_not_in_code_or_unknown():
    text = html_of("`{OK}` と {XYZ}")
    assert "<code>{OK}</code>" in text
    assert "{XYZ}" in text
    assert "badge" not in text


def test_checklist():
    text = html_of("- [ ] まだの項目\n- [x] 済んだ項目\n")
    boxes = [attrs for tag, attrs in tags_of(text) if tag == "input"]
    assert len(boxes) == 2
    assert all(attrs["type"] == "checkbox" and "disabled" in attrs for attrs in boxes)
    assert ["checked" in attrs for attrs in boxes] == [False, True]
    assert "[ ]" not in text and "[x]" not in text
    assert text.count('<li class="list-none">') == 2
    # 間を空けた箇条 (li の中に段落) でも同じ。
    loose = html_of("- [ ] 一つ目\n\n- [X] 二つ目\n")
    boxes = [attrs for tag, attrs in tags_of(loose) if tag == "input"]
    assert ["checked" in attrs for attrs in boxes] == [False, True]


def test_timeline():
    text = html_of("- 2026-09-01 [変更] 治具を替えた — 位置決めのピンを新しくした。\n- 2026-09-02 [トラブル] 寸法の外れ — 刃物の摩耗が原因だった。\n")
    assert '<time datetime="2026-09-01">2026-09-01</time>' in text
    assert '<span class="badge badge-sm badge-info">変更</span>' in text
    assert '<span class="badge badge-sm badge-error">トラブル</span>' in text
    assert "<strong>治具を替えた</strong>" in text
    assert "<span>位置決めのピンを新しくした。</span>" in text
    assert text.count('<li class="list-none">') == 2


def test_photo_card():
    text = html_of("![治具の写真](image:7)\n> 赤い印の所を押さえる", images={7: "/manuals/images/7/"})
    names = [tag for tag, _attrs in tags_of(text)]
    assert names == ["figure", "img", "figcaption"]
    assert f'<figure class="{manual_markdown.CLASSES["figure"]}">' in text
    assert f'<figcaption class="{manual_markdown.CLASSES["figcaption"]}">赤い印の所を押さえる</figcaption>' in text
    assert "<blockquote" not in text


def test_image_numbers():
    body = (
        "![知っている](image:5)\n\n![知らない番号](image:99)\n\n"
        "![外の画像](https://example.invalid/a.png)\n\n![埋め込み](data:image/png;base64,AAAA)"
    )
    text = html_of(body, images={5: "/manuals/images/5/"})
    images = [attrs for tag, attrs in tags_of(text) if tag == "img"]
    assert images == [{"alt": "知っている", "loading": "lazy", "src": "/manuals/images/5/"}]
    for alt in ("知らない番号", "外の画像", "埋め込み"):
        assert f"[画像: {alt}]" in text
    assert "example.invalid" not in text
    assert "data:" not in text


@pytest.mark.parametrize(
    ("href", "allowed"),
    [
        ("http://example.invalid/a", True),
        ("https://example.invalid/a", True),
        ("mailto:someone@example.invalid", True),
        ("#step-1", True),
        ("/manuals/1/", True),
        ("javascript:alert(1)", False),
        ("JaVaScRiPt:alert(1)", False),
        ("&#106;avascript:alert(1)", False),
        ("data:text/html,abc", False),
        ("vbscript:msgbox(1)", False),
        ("//example.invalid/a", False),
    ],
)
def test_links_allowlist(href, allowed):
    text = html_of(f"[リンクの字]({href})")
    links = [attrs for tag, attrs in tags_of(text) if tag == "a"]
    assert "リンクの字" in text
    if allowed:
        assert links == [{"href": href.replace("&#106;", "j")}]
    else:
        assert links == []
        assert "href" not in text


def test_raw_html_and_script_are_safe():
    body = (
        "<script>alert(1)</script>\n\n[x](javascript:alert(1)) [y](&#106;avascript:alert(1))\n\n"
        "<img src=x onerror=alert(1)>"
    )
    text = html_of(body)
    assert "<script" not in text
    assert "javascript:" not in text
    assert "<img" not in text
    assert "&lt;script&gt;" in text
    parsed = tags_of(text)
    assert all(tag != "script" for tag, _attrs in parsed)
    assert all(not name.startswith("on") for _tag, attrs in parsed for name in attrs)


def test_code_span_is_not_double_escaped():
    assert "<code>a &lt; b</code>" in html_of("`a < b`")


def test_step_numbers_and_toc():
    body = "## 準備\n\n### 細かい所\n\n:::注意\n## 枠の中の見出し\n:::\n\n## 片付け\n"
    steps = render(body, images={}, manual_format="steps")
    assert '<h2 id="step-1"><span class="mr-2 badge badge-primary">1</span>準備</h2>' in steps.html
    assert '<h2 id="step-2"><span class="mr-2 badge badge-primary">2</span>片付け</h2>' in steps.html
    assert steps.toc == (TocEntry("1", "準備", "step-1"), TocEntry("2", "片付け", "step-2"))
    page = render(body, images={}, manual_format="page")
    assert '<h2 id="section-1"><span class="mr-2 text-base-content/50">i</span>準備</h2>' in page.html
    assert [(e.number, e.anchor) for e in page.toc] == [("i", "section-1"), ("ii", "section-2")]
    assert [roman(n) for n in (1, 2, 3, 4, 5, 9, 14, 40)] == ["i", "ii", "iii", "iv", "v", "ix", "xiv", "xl"]


def test_classes_listed_in_body_template():
    text = BODY_TEMPLATE.read_text(encoding="utf-8")
    for table in manual_markdown.CLASS_TABLES:
        for value in table.values():
            assert value in text, value
