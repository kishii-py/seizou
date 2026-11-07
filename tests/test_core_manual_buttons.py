"""画面ごとの説明書の断片が引くボタンの字と、その画面の form のボタンの字が合うか (W03・ONB-05。画面の直しの指示書 20261107 の 8)。

画面の目録 (seizou.core.screens.all_screens) の form のある画面ごとに、素の form のボタンの字 (数と金額と記録の番号を除いた字) を決め、
標準の組 (src/seizou/core/manual_default/) と 2 社の種 (examples/companies/<id>/manual/) の断片の「できること」と突き合わせる。
  (a) 断片が「保存」で・「保存」を押す と書いてよいのは、ボタンの字が「保存」の画面だけ (画面に無い「保存」を押すよう書かない)。
  (b) ボタンの字が「保存」でない画面の断片は、その字を「…」で引く (ボタンの字を変えたら、断片を直すまで落ちる)。

ボタンの字の決め方は入力のカード (core/_form_card.html) と同じ順: submit_label_plain → submit_label → 「保存」。
業務マニュアルの確認の画面 (manuals/confirm.html) の form は confirm_label。class の属性が字の物 (取り消す・削除・取り込む など) はその字
(開いた画面では記録の番号が前に付くが、断片は番号を書けない)。属性が property の物 (数と金額を入れる型の form) は form を作って読む。
記録のある画面 (URL に pk のある物) は、空の記録を渡して作る (受注の編集は「保存」、新規登録は「受注する」のように字が分かれるため)。
作れない form は class の属性の字 (無ければ「保存」)。

見ない物: template にボタンの字を直書きする画面 (削除の確かめ・見た目の設定・業務マニュアルの編集・メモ・GitHub の設定) は、form の字が
「保存」の既定のままで、断片も「保存」をボタンとして書かないので、ここでは当たらない。ボタンの字にいつも記録の値 (版の番号) が入る画面は
UNDECIDED に置く。
"""

import re

import pytest
from django.urls import get_resolver

from seizou.core.manual import fragment_name
from seizou.core.screens import _walk, all_screens
from seizou.core.views import _accepts_instance
from tests import test_examples_manual as seed_tests
from tests.test_core_manual_default import DEFAULT_DIR, DEFAULT_ID

DEFAULT_LABEL = "保存"
#: ボタンの字にいつも記録の値が入り、断片に書けない画面 (発注の変更は「第 n 版の変更を送る」)
UNDECIDED = ("purchasing_purchaseorder_change",)
SAYS_SAVE = re.compile(r"「保存」(で|を押)")
PK_IN_PATH = re.compile(r"<\w+:pk>")


def manual_folder(set_id: str):
    return DEFAULT_DIR if set_id == DEFAULT_ID else seed_tests.manual_folder(set_id)


def _form(view, form_class, with_record: bool):
    """素の form。記録のある画面は空の記録を渡す。作れなければ None。"""
    try:
        if with_record and getattr(view, "model", None) is not None and _accepts_instance(form_class):
            return form_class(instance=view.model())
        return form_class()
    except Exception:  # 要る引数が無い・空の記録では作れない form は、class の属性の字で決める
        return None


def button_label(view, form_class, with_record: bool) -> str:
    for name in ("confirm_label", "submit_label"):
        value = getattr(form_class, name, None)
        if isinstance(value, str):
            return value
    form = _form(view, form_class, with_record)
    if form is not None:
        for name in ("confirm_label", "submit_label_plain", "submit_label"):
            if hasattr(form, name):
                return str(getattr(form, name))
        return DEFAULT_LABEL
    plain = getattr(form_class, "submit_label_plain", None)
    return plain if isinstance(plain, str) else DEFAULT_LABEL


def button_labels() -> dict[str, str]:
    """断片の名前 → その画面のボタンの字 (form のある画面だけ)。"""
    views = {url_name: view_class for view_class, url_name, _path in _walk(get_resolver().url_patterns)}
    labels = {}
    for screen in all_screens():
        view = views[screen.url_name]
        form_class = getattr(view, "form_class", None)
        if form_class is None:
            continue
        labels[fragment_name(screen.url_name)] = button_label(view, form_class, bool(PK_IN_PATH.search(screen.path)))
    return labels


def can_do_text(raw: str) -> str:
    """断片の「できること」の、タグを除いた字 (無ければ断片の全部)。"""
    match = re.search(r"<h3>できること</h3>\s*<ul>(.*?)</ul>", raw, re.S)
    return seed_tests.plain(match.group(1) if match else raw)


@pytest.mark.django_db
@pytest.mark.parametrize("set_id", (DEFAULT_ID, *seed_tests.COMPANY_IDS))
def test_fragment_button_word_matches_screen(set_id):
    labels = button_labels()
    assert {"orders_order_create": "受注する", "orders_order_update": DEFAULT_LABEL, "shipping_shipment_create": "出荷する"}.items() <= labels.items()
    problems = []
    for name, label in labels.items():
        if name in UNDECIDED:
            continue
        path = manual_folder(set_id) / f"{name}.html"
        if not path.exists():
            continue
        text = can_do_text(path.read_text(encoding="utf-8"))
        where = f"{set_id}/{name}"
        if label != DEFAULT_LABEL and SAYS_SAVE.search(text):
            problems.append(f"{where}: 画面のボタンは「{label}」なのに「保存」を押すよう書いている")
        if label != DEFAULT_LABEL and f"「{label}」" not in text:
            problems.append(f"{where}: 画面のボタンの字「{label}」を「できること」に書いていない")
    assert problems == [], "\n".join(problems)
