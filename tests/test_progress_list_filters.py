"""実績の一覧の絞り (N117 の手本。期間・工程・部品・入れた人)。名前は作り物だけ。"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from seizou.progress import services
from tests.test_progress_views import client_logged_in, step, user  # noqa: F401 - fixture を借りる

TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.mark.django_db
def test_record_list_has_four_filters_and_period_includes_the_whole_last_day(client_logged_in, user, step):  # noqa: F811
    early = services.record_progress(step, Decimal("1"), datetime(2026, 11, 2, 9, 0, tzinfo=TOKYO), user)
    late = services.record_progress(step, Decimal("2"), datetime(2026, 11, 3, 23, 0, tzinfo=TOKYO), user)
    after = services.record_progress(step, Decimal("3"), datetime(2026, 11, 4, 0, 30, tzinfo=TOKYO), user)
    url = reverse("progress:record_list")

    body = client_logged_in.get(url).content.decode()
    for name in ("f_recorded_from", "f_recorded_to", "f_process", "f_item", "f_user"):
        assert f'name="{name}"' in body
    for label in ("期間", "工程", "部品", "入れた人"):
        assert f">{label}</label>" in body
    process = step.process
    item = step.workorder.order_line.item
    assert f'<option value="{process.pk}">{process.name}</option>' in body  # 工程の候補の字は名前だけ (N142)
    assert f'<option value="{item.pk}">{item}</option>' in body
    assert f'<option value="{user.pk}">{user}</option>' in body

    response = client_logged_in.get(url, {"f_recorded_from": "2026-11-02", "f_recorded_to": "2026-11-03"})
    assert [row["pk"] for row in response.context["rows"]] == [late.pk, early.pk]
    assert response.context["paginator"].count == 2

    response = client_logged_in.get(url, {"f_recorded_from": "2026-11-04", "f_process": str(process.pk), "f_item": str(item.pk), "f_user": str(user.pk)})
    assert [row["pk"] for row in response.context["rows"]] == [after.pk]
