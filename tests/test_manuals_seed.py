"""業務マニュアルの 2 社の種 (manuals.demo_seed.load)。"""

import pytest
from django.contrib.auth import get_user_model

from seizou.manuals import demo_seed, services
from seizou.manuals.models import Manual, ManualFolder, ManualVersion


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def test_seed_a_machining(user, tmp_path):
    assert demo_seed.load(tmp_path / "a_machining", user) == {"入れた": 2, "飛ばした": 0}
    assert sorted(ManualFolder.objects.values_list("name", flat=True)) == ["出荷", "段取り"]
    assert ManualFolder.objects.get(name="段取り").color == "blue"
    assert Manual.objects.filter(format="steps").count() == 2
    setup = Manual.objects.get(title="段取り替えの手順 (NC 旋盤)")
    assert setup.folder.name == "段取り" and setup.version == 2
    version = ManualVersion.objects.get()
    assert (version.manual, version.number, version.note) == (setup, 1, "手順 3 に確かめる項目を足した")
    assert "声に出して読んだ" in setup.body and "声に出して読んだ" not in version.body
    assert Manual.objects.get(title="出荷前の梱包の手順").folder.name == "出荷"


def test_seed_b_plating(user, tmp_path):
    assert demo_seed.load(tmp_path / "b_plating", user) == {"入れた": 1, "飛ばした": 0}
    assert list(ManualFolder.objects.values_list("name", "color")) == [("品質", "red")]
    manual = Manual.objects.get()
    assert (manual.title, manual.format) == ("品質の基準書 (めっき)", "page")


@pytest.mark.parametrize(("company", "count"), [("a_machining", 2), ("b_plating", 1)])
def test_seed_twice_does_not_duplicate(user, tmp_path, company, count):
    demo_seed.load(tmp_path / company, user)
    counts = (ManualFolder.objects.count(), Manual.objects.count(), ManualVersion.objects.count())
    assert demo_seed.load(tmp_path / company, user) == {"入れた": 0, "飛ばした": count}
    assert (ManualFolder.objects.count(), Manual.objects.count(), ManualVersion.objects.count()) == counts


def test_unknown_company_is_noop(user, tmp_path):
    assert demo_seed.load(tmp_path / "c_unknown", user) == {"入れた": 0, "飛ばした": 0}
    assert ManualFolder.objects.count() == 0 and Manual.objects.count() == 0


def test_seed_bodies_render_numbered(user, tmp_path):
    demo_seed.load(tmp_path / "a_machining", user)
    demo_seed.load(tmp_path / "b_plating", user)
    for manual in Manual.objects.all():
        rendered = services.render_body(manual)
        assert len(rendered.toc) >= 4, manual.title
        assert "[画像:" not in rendered.html
        assert ":::" not in rendered.html
        if manual.format == "steps":
            assert '<span class="mr-2 badge badge-primary">1</span>' in rendered.html
        else:
            assert '<span class="mr-2 text-base-content/50">i</span>' in rendered.html
