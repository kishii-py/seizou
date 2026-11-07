"""会社どうしで送り合う項目の表 (docs/edi-items.md) を、connect が送る JSON の鍵と model の欄に突き合わせるテスト。

中小企業共通 EDI 標準仕様書の本編 7.3.3「EDI データの桁数属性」は、業務アプリが送り出す桁数と取り込む桁数を公開するよう求める。
公開した表が、送る鍵を足した変更や欄の字数を変えた変更の後も、作品と同じであることを見る。
- (a) connect が送る種類 (kind) と、文書の種類の節と「知らせの種類」の表と、下の KIND_KEYS がそろう
- (b) 種類ごとに、送る JSON の鍵 (送る関数の試験の定数) と文書の表の行の鍵がそろう
- (c) 表に書いた欄が model にあり、送る字数・桁と受け取れる字数・桁が欄の字数と同じで、送る側の欄が受ける側の欄に入る
- (d) 共通辞書の ID のセルの形
- (e) 文書の言葉の決まり
文書の種類の節の表は 8 つのセル (JSON の鍵・意味・送る側の元の欄・送る字数・桁・受ける側が入れる欄・受け取れる字数・桁・EDI の項目・共通辞書の ID)。
"""

import ast
import re
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from seizou.connect import services
from tests.test_connect_acceptance import ACCEPTANCE_KEYS
from tests.test_connect_chat import CHAT_KEYS
from tests.test_connect_chat_files import CHAT_FILE_KEYS
from tests.test_connect_reply import REPLY_KEYS, REPLY_LINE_KEYS
from tests.test_connect_self_invoice import SELF_INVOICE_KEYS, SELF_INVOICE_LINE_KEYS
from tests.test_connect_send import LINE_KEYS, ORDER_KEYS
from tests.test_connect_shipping_notice import NOTICE_KEYS
from tests.test_docs_phrases import BANNED_PHRASES
from tests.test_wording import BANNED, BUTTON_OLD, RECEIVED_OLD

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "edi-items.md"
CONNECT = ROOT / "src" / "seizou" / "connect"

#: 種類 → (頭の鍵の組, {並びの鍵: 並びの 1 つの中の鍵の組})。鍵の組は送る関数の試験の定数をそのまま使う (鍵を変えたら、その試験の定数を直す)
KIND_KEYS = {
    "order": (ORDER_KEYS, {"lines": LINE_KEYS}),
    "order_reply": (REPLY_KEYS, {"lines": REPLY_LINE_KEYS}),
    "shipping_notice": (NOTICE_KEYS, {}),
    "acceptance": (ACCEPTANCE_KEYS, {}),
    "chat_message": (CHAT_KEYS | {"supplier", "attachments"}, {"attachments": CHAT_FILE_KEYS}),
    "self_invoice": (SELF_INVOICE_KEYS, {"lines": SELF_INVOICE_LINE_KEYS}),
}
#: 送り手の会社の辞書の鍵 (buyer か supplier)。注文の supplier だけは取引先マスターの {code, name} (tests/test_connect_send.py と同じ)
PARTY_KEYS = ("buyer", "supplier")
ORDER_SUPPLIER_KEYS = {"code", "name"}
#: 送る辞書と見なす鍵 (知らせの番号・種類・送った日時)
SENT_MARKS = {"id", "kind", "sent_at"}

SECTION = re.compile(r"^## (.+) \(kind = ([a-z_]+)\)$")
CODE_SECTION = "### 知らせの種類 (kind)"
ROW_HEAD = "| `"
KEY_CELL = re.compile(r"`([^`]+)`")
FIELD_REF = re.compile(r"`([a-z_]+)\.([A-Z][A-Za-z0-9]*)\.([a-z_0-9]+)`")
DICTIONARY_ID = re.compile(r"UN\d{8}|JPS\d{7}|—")
CELLS = 8
# セルの位置 (0 から)
SEND_FIELD, SEND_LIMIT, RECEIVE_FIELD, RECEIVE_LIMIT, DICTIONARY = 2, 3, 4, 5, 7


def _doc_lines():
    return DOC.read_text(encoding="utf-8").splitlines()


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _sections():
    """文書の種類の節。返す物は [(kind, 節の名前, [(行番号, 鍵, 8 つのセル)])] (見出しの順)。

    `## <字> (kind = <kind>)` の見出しから次の `## ` の見出しまでの、「| `」で始まる行を表の行にする。
    """
    sections = []
    current = None
    for number, line in enumerate(_doc_lines(), 1):
        if line.startswith("## "):
            match = SECTION.match(line)
            current = None
            if match:
                current = (match.group(2), f"{match.group(1)} (kind = {match.group(2)})", [])
                sections.append(current)
            continue
        if current is None or not line.startswith(ROW_HEAD):
            continue
        cells = _cells(line)
        assert len(cells) == CELLS, (
            f"docs/edi-items.md:{number} ({current[1]} の表) のセルが {len(cells)} つです。"
            f"{CELLS} つにしてください (セルの中に | を書かない)。"
        )
        key = KEY_CELL.fullmatch(cells[0])
        assert key, f"docs/edi-items.md:{number} ({current[1]} の表) の 1 つ目のセルは、JSON の鍵を ` で囲んで書いてください。"
        current[2].append((number, key.group(1), cells))
    return sections


def _rows_by_kind():
    return {kind: rows for kind, _title, rows in _sections()}


def _titles():
    return {kind: title for kind, title, _rows in _sections()}


def _code_table_kinds():
    """「知らせの種類 (kind)」の表の 1 つ目のセルの kind の組。"""
    kinds = set()
    inside = False
    for line in _doc_lines():
        if line.startswith("#"):
            inside = line.strip() == CODE_SECTION
            continue
        if inside and line.startswith(ROW_HEAD):
            key = KEY_CELL.fullmatch(_cells(line)[0])
            if key:
                kinds.add(key.group(1))
    return kinds


def _sent_kinds():
    """src/seizou/connect の下の .py (migrations を除く) の、鍵に字の "id"・"kind"・"sent_at" を持つ辞書の kind の値の組。"""
    kinds = set()
    for path in sorted(CONNECT.rglob("*.py")):
        if "migrations" in path.relative_to(CONNECT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            # 鍵が None の物 (** で広げた辞書) と字でない鍵は飛ばす
            values = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if not SENT_MARKS <= set(values):
                continue
            kind = values["kind"]
            assert isinstance(kind, ast.Constant) and isinstance(kind.value, str), (
                f"{path.relative_to(ROOT)}:{node.lineno} の送る辞書の kind の値は字で書いてください "
                "(docs/edi-items.md の節と突き合わせるため)。"
            )
            kinds.add(kind.value)
    return kinds


def _limit_text(field):
    """欄の字数・桁の字 (文書の「送る字数・桁」「受け取れる字数・桁」のセルと比べる)。TextField は字数が無いので None。"""
    # DateTimeField は DateField の子、UUIDField は max_length を持つので、先に見る
    if isinstance(field, models.DateTimeField):
        return "日時"
    if isinstance(field, models.DateField):
        return "日付"
    if isinstance(field, models.UUIDField):
        return "uuid (36 字)"
    if isinstance(field, models.CharField):
        return f"{field.max_length} 字"
    if isinstance(field, models.DecimalField):
        return f"整数 {field.max_digits - field.decimal_places} 桁・小数 {field.decimal_places} 桁"
    if isinstance(field, models.PositiveIntegerField):
        return "正の整数"
    if isinstance(field, models.TextField):
        return None
    pytest.fail(
        f"{field.model._meta.label}.{field.name} の型 {type(field).__name__} の字数の書き方を _limit_text に足してください。"
    )


def _fields(cell, where, problems):
    """セルの中の `<app>.<Model>.<欄>` を全部引く。返す物は [(字, 欄)]。model に無い欄は problems に足す。"""
    found = []
    for app_label, model_name, field_name in FIELD_REF.findall(cell):
        text = f"{app_label}.{model_name}.{field_name}"
        try:
            field = apps.get_model(app_label, model_name)._meta.get_field(field_name)
        except (LookupError, FieldDoesNotExist):
            problems.append(f"{where}の {text} が model にありません。")
            continue
        found.append((text, field))
    return found


def _fits(sent, received):
    """送る側の欄の値が、受ける側の欄にそのまま入るか (型が同じで、字数と桁が受ける側以下)。"""
    if sent.get_internal_type() != received.get_internal_type():
        return False
    if isinstance(sent, models.CharField):
        return sent.max_length <= received.max_length
    if isinstance(sent, models.DecimalField):
        return (
            sent.max_digits - sent.decimal_places <= received.max_digits - received.decimal_places
            and sent.decimal_places <= received.decimal_places
        )
    return True


def test_sent_kinds_match_doc_sections():
    """(a) connect が送る種類と、文書の種類の節・「知らせの種類」の表・KIND_KEYS がそろう。"""
    sent = _sent_kinds()
    assert sent, "src/seizou/connect に送る辞書 (鍵 id・kind・sent_at を持つ辞書) が見つかりません。"
    headings = [kind for kind, _title, _rows in _sections()]
    problems = [
        f"docs/edi-items.md に kind = {kind} の節が 2 つあります。1 つにしてください。"
        for kind in sorted({kind for kind in headings if headings.count(kind) > 1})
    ]
    sides = (
        ("docs/edi-items.md に kind = {kind} の節", set(headings)),
        ("docs/edi-items.md の「知らせの種類 (kind)」の表に {kind} の行", _code_table_kinds()),
        ("tests/test_connect_edi_items.py の KIND_KEYS に {kind} の鍵の組", set(KIND_KEYS)),
    )
    for place, kinds in sides:
        problems += [place.format(kind=kind) + "がありません (connect が送る)。" for kind in sorted(sent - kinds)]
        problems += [place.format(kind=kind) + "がありますが、connect が送る種類にありません。" for kind in sorted(kinds - sent)]
    assert not problems, "\n".join(problems)


@pytest.mark.django_db
def test_doc_rows_match_sent_keys():
    """(b) 種類ごとに、文書の表の行の鍵と送る JSON の鍵がそろう (頭の鍵・並びの中の鍵・送り手の会社の辞書の鍵)。"""
    party = set(services._party())
    rows_by_kind = _rows_by_kind()
    titles = _titles()
    problems = []
    for kind, (head_keys, lists) in KIND_KEYS.items():
        title = titles.get(kind, f"kind = {kind}")
        where = f"docs/edi-items.md の {title} の表"
        keys = [key for _number, key, _cells in rows_by_kind.get(kind, [])]
        problems += [f"{where}に鍵 {key} の行が 2 つあります。" for key in sorted({key for key in keys if keys.count(key) > 1})]

        expected = {}
        for head in head_keys:
            if head in lists:
                expected.update({f"{head}[].{inner}": head for inner in lists[head]})
            elif head in PARTY_KEYS:
                inner_keys = ORDER_SUPPLIER_KEYS if (kind, head) == ("order", "supplier") else party
                expected.update({f"{head}.{inner}": head for inner in inner_keys})
            else:
                expected[head] = head
        problems += [f"{where}に鍵 {key} の行がありません (送る JSON にある)。" for key in sorted(set(expected) - set(keys))]
        problems += [f"{where}に鍵 {key} の行がありますが、送る JSON にありません。" for key in sorted(set(keys) - set(expected))]
    assert not problems, "\n".join(problems)


def test_doc_fields_and_limits_match_models():
    """(c) 表に書いた欄が model にあり、字数・桁のセルが欄の字数と同じで、送る側の欄が受ける側の欄に入る。"""
    problems = []
    for _kind, title, rows in _sections():
        for number, key, cells in rows:
            where = f"docs/edi-items.md:{number} の {title} の {key} の行"
            sent = _fields(cells[SEND_FIELD], f"{where}の送る側の元の欄", problems)
            received = _fields(cells[RECEIVE_FIELD], f"{where}の受ける側が入れる欄", problems)
            for side, found, limit_cell, column in (
                ("送る側", sent, cells[SEND_LIMIT], "送る字数・桁"),
                ("受ける側", received, cells[RECEIVE_LIMIT], "受け取れる字数・桁"),
            ):
                for text, field in found:
                    limit = _limit_text(field)
                    if limit is not None and limit != limit_cell:
                        problems.append(
                            f"{where}の{column}「{limit_cell}」が、{side}の欄 {text} の字数「{limit}」と違います。"
                        )
            for sent_text, sent_field in sent:
                for received_text, received_field in received:
                    if not _fits(sent_field, received_field):
                        problems.append(
                            f"{where}: 送る側の欄 {sent_text} の値が、受ける側の欄 {received_text} に入りません (型か字数か桁が違う)。"
                        )
    assert not problems, "\n".join(problems)


def test_doc_dictionary_ids_have_the_shape():
    """(d) 種類の節の表の共通辞書の ID は、UN と 8 桁の数字・JPS と 7 桁の数字・「—」のどれか。"""
    problems = [
        f"docs/edi-items.md:{number} の {title} の {key} の行の共通辞書の ID「{cells[DICTIONARY]}」は、"
        "UN と 8 桁の数字・JPS と 7 桁の数字・「—」のどれかにしてください (説明は EDI の項目の列に書く)。"
        for _kind, title, rows in _sections()
        for number, key, cells in rows
        if not DICTIONARY_ID.fullmatch(cells[DICTIONARY])
    ]
    assert not problems, "\n".join(problems)


def test_doc_wording():
    """(e) 文書に書かない字が無く、書く字がある。"""
    text = DOC.read_text(encoding="utf-8")
    for phrase in [*BANNED_PHRASES, BANNED, BUTTON_OLD, RECEIVED_OLD, "<!--"]:
        assert phrase not in text, f"docs/edi-items.md に「{phrase}」があります。"
    for phrase in ("項目を元にした", "認証を受けた業務アプリではありません", "ver.4.3_r0"):
        assert phrase in text, f"docs/edi-items.md に「{phrase}」がありません。"
