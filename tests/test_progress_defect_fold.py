"""実績の入力と編集の不良の行を、開け閉めできる枠「不良を入れる」に入れ、初めは閉じて描く (本人の決め 11-11 22:1x)。

不良の区分を登録した会社では、不良の行 (区分・数・原因の 3 組) が「実績の数」の後に並び、欄が 17 個になって、スマホでは「記録する」まで
2 画面あまり送っていた。現場で入れる実績の多くは不良が無いので、不良の行は「不良を入れる」を押した時だけ開く。
値のある行を見えないまま保存させないために、今の不良の行がある時 (編集)・送った値に不良の行の字がある時・不良の行の欄に誤りがある時は、
開いたまま描く。閉じていても欄は form の中にあり、値は送られる。
枠は入力の土台のカード (core/_form_card.html の fold_fields・fold_label・fold_open) が描く。不良の行の欄の無い form と、ほかの入力の画面は今のまま。
作り物の区分と実績は tests/test_progress_defect_form.py の道具で作る。
"""

import re

import pytest
from django.urls import reverse

from seizou.progress.forms import DEFECT_FOLD_LABEL
from seizou.progress.models import Progress
from tests import test_progress_defect_form as base
from tests.test_progress_defect_form import LINE_FIELDS, MODAL, _edit_url, _post

# 不良の行の入力の試験の fixture を借りる (pytest は module の名前で fixture を見つける)
user = base.user
client_logged_in = base.client_logged_in
step = base.step
scratch = base.scratch
size = base.size
split_record = base.split_record

FOLD = re.compile(r'<details class="group rounded-lg bg-base-200 p-4 sm:col-span-2" data-form-fold( open)?>(.*?)</details>', re.S)


def _fold(body):
    """枠 (1 つだけ) の (開いているか, 中身の HTML, 枠の前の HTML, 枠の後の HTML)。"""
    found = list(FOLD.finditer(body))
    assert len(found) == 1, len(found)
    match = found[0]
    return bool(match.group(1)), match.group(2), body[: match.start()], body[match.end() :]


@pytest.mark.django_db
def test_new_record_form_draws_the_defect_lines_folded_and_closed(client_logged_in, step, scratch):
    body = client_logged_in.get(reverse("progress:progress_create"), {"workorder_step": step.pk}).content.decode()

    opened, inside, before, after = _fold(body)
    assert opened is False
    assert f">{DEFECT_FOLD_LABEL}</span>" in inside
    # 不良の行の 9 つの欄は枠の中だけにあり、実績の数は枠の前、記録の日時とメモは枠の後
    for name in LINE_FIELDS:
        assert f'name="{name}"' in inside, name
        assert f'name="{name}"' not in before and f'name="{name}"' not in after, name
    assert 'name="qty"' in before and 'name="qty"' not in inside
    assert 'name="recorded_at"' in after and 'name="note"' in after


@pytest.mark.django_db
def test_fold_is_also_in_the_modal(client_logged_in, step, scratch):
    body = client_logged_in.get(reverse("progress:progress_create"), {"workorder_step": step.pk}, **MODAL).content.decode()

    opened, inside, _before, _after = _fold(body)
    assert opened is False
    assert all(f'name="{name}"' in inside for name in LINE_FIELDS)


@pytest.mark.django_db
def test_fold_opens_when_a_defect_line_has_an_error_or_a_value(client_logged_in, step, scratch):
    # 区分だけを選んで数が空 = 不良の行の欄の誤りで止まる: 開いたまま描き直す
    response = _post(client_logged_in, step, defect_1_kind=scratch.pk)
    assert response.status_code == 200 and not Progress.objects.exists()
    assert _fold(response.content.decode())[0] is True

    # 数だけを入れて区分が空 = ポカよけ R30 の確かめで戻る (誤りではない): 送った値があるので開いたまま
    response = _post(client_logged_in, step, defect_1_qty="1")
    assert response.status_code == 200 and not Progress.objects.exists()
    assert _fold(response.content.decode())[0] is True


@pytest.mark.django_db
def test_fold_stays_closed_when_only_another_field_is_wrong(client_logged_in, step, scratch):
    # 実績の数が空 (不良の行には何も入れていない): 枠は閉じたまま
    response = _post(client_logged_in, step, qty="")
    assert response.status_code == 200 and not Progress.objects.exists()
    assert response.context["form"].errors.get("qty")
    assert _fold(response.content.decode())[0] is False


@pytest.mark.django_db
def test_edit_opens_the_fold_only_when_the_record_has_defect_rows(client_logged_in, user, step, split_record, scratch, size):
    # 不良の行のある実績の編集: 開いて描く (今の行が見える)
    opened, inside, _before, _after = _fold(client_logged_in.get(_edit_url(split_record)).content.decode())
    assert opened is True
    assert 'name="defect_1_qty"' in inside and 'name="defect_5_cause"' in inside

    # 不良の無い実績の編集: 閉じて描く
    from decimal import Decimal

    from seizou.progress import services

    plain = services.record_progress(step, Decimal("1"), base.RECORDED_AT, user)
    assert _fold(client_logged_in.get(_edit_url(plain)).content.decode())[0] is False


@pytest.mark.django_db
def test_no_fold_without_defect_line_fields(client_logged_in, step):
    # 使用中の不良の区分が無い会社: 今の「不良の数」の欄のままで、枠は無い
    body = client_logged_in.get(reverse("progress:progress_create"), {"workorder_step": step.pk}).content.decode()
    assert 'name="defect_qty"' in body
    assert "data-form-fold" not in body
    # ほかの入力の画面にも枠は無い
    assert "data-form-fold" not in client_logged_in.get(reverse("masters:partner_create")).content.decode()
