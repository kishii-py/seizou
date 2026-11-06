"""工程の画面 (一覧・詳細・入れる・直す。core の土台の上)。「計画に使う」の印を画面で付け外しする。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters.models import Process


@pytest.fixture
def client_logged_in(client, db):
    user = get_user_model().objects.create_user(username="tester", password="tests-only")
    client.force_login(user)
    return client


@pytest.fixture
def turn(db):
    return Process.objects.create(code="TURN", name="旋削", sort_order=10, use_in_plan=True)


@pytest.fixture
def insp(db):
    return Process.objects.create(code="INSP", name="検査", sort_order=20)


def _row(html, process):
    begin = html.index(f'id="row-{process.pk}"')
    return html[begin : html.index("</tr>", begin)]


def _form_data(process, **changes):
    data = {"code": process.code, "name": process.name, "sort_order": process.sort_order}
    if process.use_in_plan:
        data["use_in_plan"] = "on"
    data.update(changes)
    return data


@pytest.mark.django_db
def test_process_list_shows_use_in_plan_column(client_logged_in, turn, insp):
    response = client_logged_in.get(reverse("masters:process_list"))

    assert response.status_code == 200
    html = response.content.decode()
    assert "計画に使う" in html[html.index("<thead") : html.index("</thead>")]
    assert "はい" in _row(html, turn)
    assert "いいえ" in _row(html, insp)
    assert reverse("masters:process_detail", args=[turn.pk]) in html


@pytest.mark.django_db
def test_process_create_with_use_in_plan(client_logged_in):
    response = client_logged_in.post(
        reverse("masters:process_create"),
        {"code": "MILL", "name": "フライス加工", "sort_order": "20", "use_in_plan": "on"},
    )

    process = Process.objects.get(code="MILL")
    assert process.use_in_plan is True
    assert process.is_outsourced is False
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:process_detail", args=[process.pk])


@pytest.mark.django_db
def test_process_update_sets_use_in_plan(client_logged_in, insp):
    url = reverse("masters:process_update", args=[insp.pk])
    assert client_logged_in.get(url).status_code == 200

    response = client_logged_in.post(url, _form_data(insp, use_in_plan="on"))
    assert response.status_code == 302
    insp.refresh_from_db()
    assert insp.use_in_plan is True

    data = _form_data(insp)
    data.pop("use_in_plan")
    client_logged_in.post(url, data)
    insp.refresh_from_db()
    assert insp.use_in_plan is False


@pytest.mark.django_db
def test_process_update_duplicate_code_is_red(client_logged_in, turn, insp):
    url = reverse("masters:process_update", args=[insp.pk])

    response = client_logged_in.post(url, _form_data(insp, code="TURN", name="検査 (直した)"))
    assert response.status_code == 200
    assert 'data-notice="stop"' in response.content.decode()
    insp.refresh_from_db()
    assert insp.code == "INSP"
    assert insp.name == "検査"

    response = client_logged_in.post(url, _form_data(insp, name="外観検査"))
    assert response.status_code == 302
    insp.refresh_from_db()
    assert insp.code == "INSP"
    assert insp.name == "外観検査"


@pytest.mark.django_db
def test_menu_has_process_entry(client_logged_in):
    html = client_logged_in.get(reverse("core:home")).content.decode()
    nav = html[html.index('aria-label="メニュー"') : html.index("</nav>", html.index('aria-label="メニュー"'))]
    masters = nav[nav.index(">マスター<") :]
    positions = [masters.index(f'<span class="truncate">{label}</span>') for label in ("部品マスター", "取引先", "工程")]
    assert positions == sorted(positions)
    assert f'href="{reverse("masters:process_list")}"' in masters
