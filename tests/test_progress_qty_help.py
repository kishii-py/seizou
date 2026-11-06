"""実績の新規登録の「実績の数」の欄の下の説明 (初見テスト R5 の V115。候補 C29)。

「実績の数」は、その工程を終えた良品の数 (不良を除く) を入れる。同じ form を使う画面 (実績の新規登録・生産管理ボードの
「実績を入れる」の小窓・盤の行の「実績を入力」の小窓) と、「?」の「入れる物」の表の説明の列に同じ字が出る。
比べるのは前の半分 (良品の数の文) だけ。後ろの半分 (不良の数の書き場所) は、不良の数の欄ができたら変わるので見ない。
"""

import re

import pytest
from django.urls import reverse

from seizou.core.manual import input_rows
from seizou.progress.forms import ProgressRecordForm

QTY_HELP_HEAD = "この工程を終えた良品の数 (不良を除く)。"


def test_qty_help_text_on_form():
    assert ProgressRecordForm.base_fields["qty"].help_text.startswith(QTY_HELP_HEAD)
    assert ProgressRecordForm.base_fields["qty"].label == "実績の数"  # 欄の名前は変えない


@pytest.mark.django_db
def test_qty_help_text_under_field_on_create(client_logged_in):
    response = client_logged_in.get(reverse("progress:progress_create"))
    assert response.status_code == 200
    match = re.search(r'<p id="id_qty_helptext"[^>]*>(.*?)</p>', response.content.decode(), re.S)
    assert match, "実績の数の欄の下の説明が無い"
    assert match.group(1).strip().startswith(QTY_HELP_HEAD)


def test_qty_help_text_in_manual_input_rows():
    rows = {row[0]: row for row in input_rows(ProgressRecordForm)}
    assert rows["実績の数"][2].startswith(QTY_HELP_HEAD)
