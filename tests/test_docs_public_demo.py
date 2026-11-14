"""README の「試す」の節の、公開のデモの案内を確かめる。

訳: 作者が、作り物の 2 社の置き場を外に公開した (2026-11-15)。入れずに触れる入口なので、README の「試す」の頭に置く。
デモの ID とパスワードは、ログインの画面の上の帯に出している。README には URL だけを書き、合言葉の字は書かない
(合言葉を替えた時に、README が古いまま残らないようにするため)。入れたデータが毎晩消える事と、本物の情報を入れない事は、
開く前に読める所にも書く。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_URLS = ("https://a.ts-sample.top/", "https://b.ts-sample.top/")


def _try_section() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return text.split("\n## 試す\n", 1)[1].split("\n## ", 1)[0]


def test_try_section_starts_with_the_public_demo():
    section = _try_section()
    for url in DEMO_URLS:
        assert url in section, f"「試す」の節にデモの URL が無い: {url}"
    # デモの案内は、手元に立てる命令 (git clone) より前に置く
    assert section.index(DEMO_URLS[0]) < section.index("git clone")


def test_demo_notice_says_where_the_login_is_and_what_not_to_enter():
    section = _try_section()
    assert "ログインの画面の上の帯" in section
    assert "毎晩消える" in section
    assert "本物の情報は入れない" in section
    assert "1MB" in section


def test_readme_does_not_carry_the_demo_passphrase():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "seizou-demo-" not in text, "README に合言葉の字を書かない (ログインの画面の帯と、手引きの「触り方」にある)"
