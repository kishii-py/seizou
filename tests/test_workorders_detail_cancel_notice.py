"""指示書の詳細の取消が出ない人への訳と頼む先 (初見テスト R5 の V17・V18・U19・U20。候補 C12 の 1 と 2。決め C12-1 = a)。

1 = 「?」の説明の断片 (標準の組と 2 社の種) の取消の行を「出ない人がいる」形にする。
Django の test client で HTML を見る (本物のブラウザは使わない)。名前は作り物だけ。
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou import cli

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS = (
    ROOT / "src" / "seizou" / "core" / "manual_default" / "workorders_workorder_detail.html",
    ROOT / "examples" / "companies" / "a_machining" / "manual" / "workorders_workorder_detail.html",
    ROOT / "examples" / "companies" / "b_plating" / "manual" / "workorders_workorder_detail.html",
)
NEW_LINE = (
    "<li>「取消」で、発行の指示書を取り消す。実績の入った指示書は取り消せない。"
    "権限の設定によっては、「取消」が出ない人がいる。要るときはアカウント管理の担当者に頼む。</li>"
)
OLD_LINE = "<li>「取消」で指示書を取り消す。実績の入った指示書は取り消せない。</li>"
HTMX = {"HTTP_HX_REQUEST": "true"}


def test_fragments_say_some_people_have_no_cancel():
    """3 本はバイトで同じ。新しい行が 1 回ずつ、古い行は 0 件。「できること」の li は 4 つのまま。"""
    blobs = [path.read_bytes() for path in FRAGMENTS]
    assert blobs[0] == blobs[1] == blobs[2]
    for path in FRAGMENTS:
        text = path.read_text(encoding="utf-8")
        assert text.count(NEW_LINE) == 1, path
        assert OLD_LINE not in text, path
        can_do = re.search(r"<h3>できること</h3>\s*<ul>(.*?)</ul>", text, re.S).group(1)
        # 「できること」の行は 5 つ (取消の行を置き換えただけで数は変えない。上限 6 = test_examples_manual の test_fragment_shape)
        assert can_do.count("<li>") == 5, path


@pytest.mark.django_db
def test_modal_shows_contact_after_copy(client, settings, tmp_path):
    """A 社の種を置き場に写した後、「?」の小窓 (HX-Request) の中身に頼む先が出る。"""
    settings.BASE_DIR = tmp_path
    user = get_user_model().objects.create_user(username="tester", password="tests-only")
    client.force_login(user)
    cli.copy_manual(ROOT / "examples" / "companies" / "a_machining", tmp_path)

    response = client.get(reverse("core:manual_fragment", args=["workorders_workorder_detail"]), **HTMX)

    assert response.status_code == 200
    html = response.content.decode()
    assert "アカウント管理の担当者に頼む" in html
    assert "権限の設定によっては、「取消」が出ない人がいる。" in html
