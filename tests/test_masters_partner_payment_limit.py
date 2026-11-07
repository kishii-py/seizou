"""取引先の保存の時の、支払の条件が受領から 2 か月 (60 日) を超える形の黄の知らせ (masters.services.payment_terms_note。本人の決め 11-08 00:24。N356)。

- (a) 協力企業で 20 日締め・翌月・31 日払いは出る (保存は止めない)
- (b) 月末締め・翌月末払いは出ない
- (c) 15 日締め・翌月・15 日払いは出ない (受領 6-16 → 支払期日 8-15 = 2 か月の前日)
- (d) 31 日締め・翌々月・10 日払いは出る
- (e) 得意先だけの取引先には出ない (両方には出る)
- (f) 支払の月の空の協力企業には出ない
- (g) update_partner で翌月 → 翌々月に直すと出て、表には直した値が残る
- (h) 取引先の編集の画面から保存すると 302 で、移った先の知らせが黄 (warning)
- (i) 今日の日付 (1 月と 7 月) に依らず (a)(c)(d) の答えが同じ
取引先は作り物だけを使う。
"""

from datetime import date

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Partner

OVER = "2 か月 (60 日) を超えます"


def _data(code, **changes):
    data = {
        "code": code,
        "name": f"作り物の取引先 {code}",
        "role": "subcontractor",
        "rounding": "round",
        "tax_mode": "total",
        "tax_rounding": "round",
        "closing_day": 31,
        "payment_month": "next",
        "payment_day": 31,
    }
    data.update(changes)
    return data


def _create(user, code, **changes):
    return masters_services.create_partner(_data(code, **changes), user)


def _level(partner):
    return getattr(partner, "saved_level", "")


@pytest.fixture
def today(monkeypatch):
    """今日の日付を差し替える (payment_terms_note は timezone.localdate() の締め期間から見る)。"""

    def set_today(day):
        monkeypatch.setattr("seizou.masters.services.timezone.localdate", lambda *args, **kwargs: day)

    set_today(date(2026, 11, 8))
    return set_today


@pytest.mark.django_db
def test_twenty_closing_next_month_end_warns(user, today):
    """(a) 20 日締め・翌月・31 日払い: 受領 10-21 → 支払期日 12-31 (2 か月の前日 12-20 を超える) で黄の知らせ。保存はされる。"""
    partner = _create(user, "S201", closing_day=20)
    assert _level(partner) == "warning"
    assert partner.saved_note == (
        "この支払の条件 (締日 20 日・支払日 翌月末) では、受領日 (2026-10-21) から支払期日 (2026-12-31) までが 2 か月 (60 日) を超えます。"
        "取適法の対象の取引では、支払期日の決まりに反するおそれがあります。支払の条件を確かめてください。"
    )
    assert Partner.objects.filter(code="S201", closing_day=20, payment_month="next", payment_day=31).exists()


@pytest.mark.django_db
def test_month_end_next_month_end_is_quiet(user, today):
    """(b) 月末締め・翌月末払い (締日 31 と、締日の空) は出ない。"""
    assert _level(_create(user, "S202")) == ""
    assert _level(_create(user, "S203", closing_day=None)) == ""
    assert masters_services.payment_terms_note(Partner.objects.get(code="S202")) == ""


@pytest.mark.django_db
def test_fifteen_closing_next_fifteen_is_quiet(user, today):
    """(c) 15 日締め・翌月・15 日払い: 受領 6-16 → 支払期日 8-15 (2 か月の前日) なので出ない。"""
    today(date(2027, 6, 20))
    assert _level(_create(user, "S204", closing_day=15, payment_day=15)) == ""


@pytest.mark.django_db
def test_after_next_month_warns(user, today):
    """(d) 31 日締め・翌々月・10 日払いは出る。"""
    partner = _create(user, "S205", payment_month="after_next", payment_day=10)
    assert _level(partner) == "warning"
    assert OVER in partner.saved_note


@pytest.mark.django_db
def test_customer_only_is_quiet(user, today):
    """(e) 得意先だけの取引先は、こちらが払う側でないので出ない。両方 (得意先と協力企業) には出る。"""
    assert _level(_create(user, "C201", role="customer", closing_day=20)) == ""
    assert _level(_create(user, "B201", role="both", closing_day=20)) == "warning"


@pytest.mark.django_db
def test_empty_payment_month_is_quiet(user, today):
    """(f) 支払の月の空の協力企業は支払期日が出ないので出ない。"""
    assert _level(_create(user, "S206", payment_month="", payment_day=None)) == ""
    assert _level(_create(user, "S207", closing_day=20, payment_month="")) == ""


@pytest.mark.django_db
def test_update_to_after_next_warns_and_saves(user, today):
    """(g) 翌月 → 翌々月に直すと出て、表には直した値が保存されている (保存は止めない)。"""
    partner = _create(user, "S208")
    assert _level(partner) == ""

    updated = masters_services.update_partner(Partner.objects.get(pk=partner.pk), {"payment_month": "after_next"}, user)
    assert _level(updated) == "warning"
    assert OVER in updated.saved_note
    assert Partner.objects.get(pk=partner.pk).payment_month == "after_next"

    back = masters_services.update_partner(Partner.objects.get(pk=partner.pk), {"payment_month": "next"}, user)
    assert _level(back) == ""


@pytest.mark.django_db
def test_update_screen_shows_warning(client_logged_in, user, today):
    """(h) 取引先の編集の画面から翌々月にして保存すると 302 で、知らせは黄 (warning) で「保存しました。」の後ろに字が付く。"""
    partner = _create(user, "S209")
    form = {
        "code": "S209",
        "name": "作り物の取引先 S209",
        "role": "subcontractor",
        "billing_method": "closing",
        "closing_day": "31",
        "payment_month": "after_next",
        "payment_day": "31",
        "tax_mode": "total",
        "active": "on",
    }
    response = client_logged_in.post(reverse("masters:partner_update", args=[partner.pk]), form)

    assert response.status_code == 302
    notices = [(message.level_tag, str(message)) for message in get_messages(response.wsgi_request)]
    assert len(notices) == 1
    level, text = notices[0]
    assert level == "warning"
    assert text.startswith("保存しました。") and OVER in text
    assert Partner.objects.get(pk=partner.pk).payment_month == "after_next"


@pytest.mark.django_db
def test_answer_does_not_depend_on_today(user, today):
    """(i) 今日が 1 月でも 7 月でも、(a) は出る・(c) は出ない・(d) は出る。"""
    partners = {
        "a": _create(user, "S210", closing_day=20),
        "c": _create(user, "S211", closing_day=15, payment_day=15),
        "d": _create(user, "S212", payment_month="after_next", payment_day=10),
    }
    for day in (date(2027, 1, 15), date(2027, 7, 15)):
        today(day)
        answers = {key: bool(masters_services.payment_terms_note(Partner.objects.get(pk=row.pk))) for key, row in partners.items()}
        assert answers == {"a": True, "c": False, "d": True}, day
