"""フィードバックの種類と Issue の文の見出しを CONTRIBUTING.md の Issue の型にそろえる (決め 11-04 14:4x の 7。精査 A5 の 3 節の 3。依頼書 N108)。

種類の前の 5 つは、CONTRIBUTING.md の「## Issue の型」の 5 つの型と同じ名前と並び。値の英字は既にある 4 つ
(bug・company・common・usage) を残し、決まりの反例 (counter) と決まりの提案 (proposal) を足した。使い方は型に無いので最後。
Issue の文の本文の見出しは、その型の「次の見出しの順に書く」の箇条を CONTRIBUTING の並びのまま使う
(箇条の見出しを持たない型と使い方は、要望の見出し)。CONTRIBUTING.md を読んで比べるので、手引きの型を変えたらこの試験が知らせる。
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.feedback import services
from seizou.feedback.forms import FeedbackForm
from seizou.feedback.models import Feedback

ROOT = Path(__file__).resolve().parent.parent
#: 型の見出し (### 1. 不具合) と、型の中の箇条 (- 再現の手順 (番号つき))
TYPE_HEADING = re.compile(r"^### \d+\. (.+)$")
BULLET = re.compile(r"^- (.+)$")
#: 名前の後ろの括弧の補い (「要望 (新しい機能や画面)」の「 (新しい機能や画面)」)
NOTE = re.compile(r" \(.+\)$")
#: 既にある 4 つの値と、新しい名前
OLD_VALUES = {"usage": "使い方", "company": "業務の流れの違い", "common": "要望", "bug": "不具合"}


def contributing_types() -> dict[str, list[str]]:
    """CONTRIBUTING.md の「## Issue の型」の {型の名前: 「次の見出しの順に書く」の箇条の並び} (箇条が無い型は空の並び)。

    名前と箇条は、後ろの括弧の補いを除く。記入例の箇条は数えない。
    """
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    types: dict[str, list[str]] = {}
    current = None
    listing = False
    for line in text.split("## Issue の型", 1)[1].splitlines():
        if line.startswith("## "):
            break
        heading = TYPE_HEADING.match(line)
        if heading:
            current = NOTE.sub("", heading.group(1))
            types[current] = []
            listing = False
        elif current is not None and line.startswith("次の見出しの順に書く"):
            listing = True
        elif current is not None and line.startswith("記入例"):
            listing = False
        elif listing and BULLET.match(line):
            types[current].append(NOTE.sub("", BULLET.match(line).group(1)))
    return types


@pytest.fixture
def writer(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.mark.django_db
def test_kinds_follow_contributing_types():
    names = list(contributing_types())
    labels = [label for _, label in Feedback.KIND_CHOICES]

    # 前の 5 つは CONTRIBUTING.md の型の名前と並び。使い方 (型に無い) だけが最後に足してある。
    assert len(names) == 5
    assert labels[:5] == names
    assert Feedback.KIND_CHOICES[5:] == [("usage", "使い方")]
    # 既にある 4 つの値は残す (既にある行を壊さない)。新しい値は列の長さ (10 字) に収まる。
    assert set(OLD_VALUES) <= set(services.KINDS)
    assert all(len(value) <= Feedback._meta.get_field("kind").max_length for value in services.KINDS)
    # 入力の画面の選択肢と断りの文も同じ並び。
    assert [label for value, label in FeedbackForm().fields["kind"].choices if value] == labels
    assert services.MESSAGE_KIND == "種類は 不具合・決まりの反例・決まりの提案・業務の流れの違い・要望・使い方 のどれかです。"


@pytest.mark.django_db
def test_issue_headings_follow_contributing(writer):
    types = contributing_types()
    data = {"title": "作り物の題", "problem": "作り物の困りごと。", "situation": "作り物の場面。", "wish": "作り物の望む形。"}
    expected = {
        "bug": ["再現の手順", "期待した動き", "実際の動き"],
        "counter": ["操作の手順", "止まるはずの決まりの番号", "実際に起きた事"],
    }
    for value, label in Feedback.KIND_CHOICES:
        text = services.export_markdown(services.create_feedback({**data, "kind": value}, writer))
        headings = re.findall(r"^## (.+)$", text, re.M)
        # 箇条の見出しを持たない型 (決まりの提案・業務の流れの違い) と使い方は、要望の見出しを使う。
        source = types.get(label) or types["要望"]
        assert headings == expected.get(value, ["困っている場面", "欲しい動き"]), label
        # 出す見出しは、その型の見出しの中から CONTRIBUTING の並びのまま (欄で埋まらない見出しは世話役が足す)。
        assert headings == [heading for heading in source if heading in headings], label
        assert not {"困りごと", "場面", "望む形"} & set(headings), label

    # 要望で出さないのは、欄の無い「今はどうしているか」だけ。困りごとと場面は「困っている場面」に空の行で結ぶ。
    assert types["要望"] == ["困っている場面", "欲しい動き", "今はどうしているか"]
    common = services.export_markdown(services.create_feedback({**data, "kind": "common"}, writer))
    assert "## 困っている場面\n作り物の困りごと。\n\n作り物の場面。\n\n## 欲しい動き\n作り物の望む形。\n" in common
    bug = services.export_markdown(services.create_feedback({**data, "kind": "bug"}, writer))
    assert "## 再現の手順\n作り物の場面。\n\n## 期待した動き\n作り物の望む形。\n\n## 実際の動き\n作り物の困りごと。\n" in bug


@pytest.mark.django_db
def test_existing_values_show_new_names_in_list(client, writer):
    rows = {
        value: services.create_feedback({"kind": value, "title": f"{value} の題", "problem": "作り物の困りごと。"}, writer)
        for value in OLD_VALUES
    }
    client.force_login(writer)

    html = client.get(reverse("feedback:feedback_list")).content.decode()
    for value, label in OLD_VALUES.items():
        row_html = re.search(rf'<tr id="row-{rows[value].pk}".*?</tr>', html, re.S).group(0)
        assert f'title="{label}">{label}</span>' in row_html, value
    # 一覧の絞りの種類の選択肢も、表の並びと新しい名前。古い名前は画面に出ない。
    kind_select = re.search(r'<select[^>]*name="f_kind"[^>]*>.*?</select>', html, re.S).group(0)
    assert re.findall(r'<option value="(\w+)"[^>]*>([^<]+)</option>', kind_select) == Feedback.KIND_CHOICES
    assert "自社の作業" not in html and "皆の要望" not in html

    # 既にある値で絞っても、その行だけが新しい名前で出る。
    html = client.get(reverse("feedback:feedback_list"), {"f_kind": "company"}).content.decode()
    assert re.findall(r'<tr id="row-(\d+)"', html) == [str(rows["company"].pk)]
    assert 'title="業務の流れの違い">業務の流れの違い</span>' in html
