"""docs/vps.md (VPS 1 台に 2 社分を立てる手順) の形を確かめる。

標準の library だけを使い、Django を読まない。
鍵の値に見える字と書かない言い回しは、既にある文書の試験の名前を import して同じ見方で見る (字をここに写さない)。
"""

import ipaddress
import re
from pathlib import Path

from tests.test_docs_phrases import BANNED_PHRASES
from tests.test_docs_readme import SECRET_LIKE, URL, _looks_secret
from tests.test_wording import BANNED

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "vps.md"

# 段の見出し (## <番号>. <題>) と、段ごとの確かめ方の行の頭
STEP = re.compile(r"## (\d+)\. ")
CHECK = "確かめ方: "
MIN_STEPS = 15

# URL の名前 (英数字と . と - の並び。<名前> のような置き字は拾わない)
URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)")
# 名前らしい字の並び: 英数字の語を . でつなぎ、末尾が次の 5 つの物。
# ファイルの名前 (operations.md・settings.py・nginx-files.conf・db.sqlite3 など) は末尾で外れる。
LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
HOST_LIKE = re.compile(rf"(?<![A-Za-z0-9.-]){LABEL}(?:\.{LABEL})*\.(?:jp|com|net|org|io)(?![A-Za-z0-9-])")
EXAMPLE_SUFFIX = ".example.jp"
OTHER_HOSTS = {"github.com", "127.0.0.1", "localhost"}

# IPv4 の形の字と、文書の例のための 3 つの範囲 (RFC 5737)
IPV4 = re.compile(r"(?<![0-9.])\d{1,3}(?:\.\d{1,3}){3}(?!\.?[0-9])")
LOOPBACK = "127.0.0.1"
EXAMPLE_NETWORKS = [ipaddress.ip_network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]

# .env の形の行 (行の頭の空白の後に、英大文字と数字と _ の名前と =)
ENV_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", re.MULTILINE)
SECRET_NAME_PARTS = ("SECRET", "KEY", "PASSWORD")
PLACEHOLDER = re.compile(r"<[^<>\s]+>")


def _read():
    return DOC.read_text(encoding="utf-8")


def _sections(text):
    """見出し (## ) ごとに切った塊。塊は見出しの行から、次の見出しの前まで。"""
    return [part for part in re.split(r"^(?=## )", text, flags=re.MULTILINE) if part.startswith("## ")]


def _host_is_example(host):
    host = host.lower().rstrip(".")
    return host.endswith(EXAMPLE_SUFFIX) or host in OTHER_HOSTS


def _ip_is_example(text):
    if text == LOOPBACK:
        return True
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return any(address in net for net in EXAMPLE_NETWORKS)


def test_vps_every_step_has_check_line():
    steps = []
    for section in _sections(_read()):
        match = STEP.match(section)
        if match:
            steps.append((int(match.group(1)), section))
    numbers = [number for number, _ in steps]

    assert numbers == list(range(1, len(numbers) + 1)), f"段の番号が 1 から続いていない: {numbers}"
    assert len(numbers) >= MIN_STEPS, f"段が {len(numbers)} しかない ({MIN_STEPS} 以上)"
    missing = [number for number, section in steps if not any(line.startswith(CHECK) for line in section.splitlines())]
    assert not missing, f"確かめ方の行が無い段: {missing}"


def test_vps_hosts_and_ips_are_examples_only():
    text = _read()
    hosts = set(URL_HOST.findall(text)) | set(HOST_LIKE.findall(text))

    assert "a.example.jp" in hosts, "名前の拾い方が文書の例の名前を拾っていない"
    others = sorted(host for host in hosts if not _host_is_example(host))
    assert not others, f"作り物でない名前がある: {others}"
    addresses = sorted(set(IPV4.findall(text)))
    assert addresses, "IP の拾い方が文書の例の IP を拾っていない"
    others = [address for address in addresses if not _ip_is_example(address)]
    assert not others, f"文書の例の範囲でない IP がある: {others}"


def test_vps_has_no_secret_like_values():
    text = URL.sub(" ", _read())
    found = [run for run in SECRET_LIKE.findall(text) if _looks_secret(run)]
    assert not found, f"docs/vps.md に鍵の値に見える字の並びがある: {[run[:8] + '…' for run in found]}"


def test_vps_env_secret_lines_are_placeholders():
    lines = [(name, value.strip()) for name, value in ENV_LINE.findall(_read())]
    secret_lines = [(name, value) for name, value in lines if any(part in name for part in SECRET_NAME_PARTS)]

    assert secret_lines, "鍵の行の拾い方が文書の取引先の鍵の行を拾っていない"
    bad = [name for name, value in secret_lines if value and PLACEHOLDER.fullmatch(value) is None]
    assert not bad, f"鍵の行の値が置き字 (<値> の形) でない: {bad}"


def test_vps_has_no_banned_phrases():
    text = _read()
    for phrase in [*BANNED_PHRASES, BANNED]:
        assert phrase not in text, f"docs/vps.md に「{phrase}」がある"
    assert "<!--" not in text, "docs/vps.md に HTML のコメントがある"
