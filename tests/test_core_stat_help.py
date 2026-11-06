"""ダッシュボードの数字のカードの数え方の 1 文 (help) と、カードの題の横の (i) (初見ユーザーテスト R4 の 5-3。UX の案 6-E)。

見る所: 全部のカードに help があり空でない・文が数え方の式と合う (手入力の分は会社の設定の値)・画面のカードの題のすぐ後ろに (i) のボタンがあり、
ボタンの名前 (aria-label) が「<題>の数え方」で、小窓に出す字 (data-stat-help) が help と同じ・ボタンに onclick が無く、吹き出し (tooltip) も無い
(押すと小窓に出す = 候補 C30 の 2)・help の無いカードには (i) が出ない・金額を見られない人でもカードと (i) が崩れない。
Django の test client で HTML を見る。名前は作り物だけ。
"""

import re
from html import unescape

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import views

HELP = {
    "部品": "部品マスターにある部品の数です。使用を止めた部品も数えます。",
    "取引先": "登録した取引先 (得意先・協力企業・両方) の数です。使用を止めた取引先も数えます。",
    "今月の受注": "受注日が今月の受注の件数です。取消は数えません。",
    "出荷の残りがある明細": "受注の数量から出荷した数を引いた残りがある明細の数です。取消の受注は数えません。",
    "受信した注文": "受信した注文 (取引先から直接届いた物と、読み取った注文書から入った物) のうち、まだ受け入れも断りもしていない物の数です。",
    "今月の支払の見込み": "受入日が今月の受入の金額 (受け入れた数量 × 発注の単価) の合計です。",
    "今月の入金": "入金日が今月の入金の金額の合計です。取消は数えません。",
    "手入力せずに取り込んだ明細": (
        "受注日が今月の受注のうち、読み取った注文書か受信した注文から作った受注の明細の行の数です。取消は数えません。"
        "入力を省けた時間は、1 行を手入力する時間を 1 分と置いた目安です。"
    ),
}
LABEL = '<p class="text-xs font-medium text-base-content/70">{}</p>'
# 題の横の (i) のボタン。aria-label の「の数え方」の前 (題) と、data-stat-help (小窓に出す help の 1 文) を取る。
INFO = re.compile(r'<button type="button" class="[^"]*" aria-label="([^"]*)の数え方" data-stat-help="([^"]*)">')


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def home(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


def stats_part(body):
    """数字のカードの枠 (data-tour="stats" の div) から、格子の後ろの (i) の小窓の中身 (template) の手前までの HTML。

    template が無ければ次の枠 (今日やること) の手前まで。最後のカード (8 枚目) の切れ端に template と script を入れないため。
    """
    start = body.index('data-tour="stats"')
    end = body.find('<template id="stat-help-template">', start)
    if end == -1:
        end = body.find('data-tour="flow-guide"', start)
    return body[start : end if end != -1 else len(body)]


def cards(body):
    """カードごとの HTML (題の <p> から次のカードの題の手前まで)。題の字 → HTML。"""
    part = stats_part(body)
    found = list(re.finditer(r'<p class="text-xs font-medium text-base-content/70">([^<]*)</p>', part))
    return {
        unescape(match.group(1)): part[match.start() : found[index + 1].start() if index + 1 < len(found) else len(part)]
        for index, match in enumerate(found)
    }


@pytest.mark.django_db
def test_every_card_has_help_matching_how_it_counts(settings):
    settings.SEIZOU = {}

    stats = views.dashboard_stats()

    # 試験の settings は全部の部品が入っているので 8 枚
    assert [stat["label"] for stat in stats] == list(HELP)
    for stat in stats:
        assert isinstance(stat["help"], str) and stat["help"].strip(), stat["label"]
        assert stat["help"] == HELP[stat["label"]], stat["label"]


@pytest.mark.django_db
def test_hands_free_help_uses_minutes_setting(settings):
    settings.SEIZOU = {"transcription_minutes_per_line": 1.5}

    card = next(stat for stat in views.dashboard_stats() if stat["label"] == "手入力せずに取り込んだ明細")

    assert "1 行を手入力する時間を 1.5 分と置いた目安です。" in card["help"]
    assert "(1 行 1.5 分で数える)" in card["note"]
    assert card["detail"] == "入力を省けた時間 ≒ 0 分"  # 数の下の 1 行はそのまま


@pytest.mark.django_db
def test_home_puts_info_next_to_each_title(logged_in, settings):
    settings.SEIZOU = {}

    body = home(logged_in)
    by_label = cards(body)

    assert list(by_label) == list(HELP)
    for label, help_text in HELP.items():
        card = by_label[label]
        # (i) は題の <p> のすぐ後ろ (題の横)
        assert re.match(re.escape(LABEL.format(label)) + r'\s*<button type="button"', card), label
        (match,) = INFO.findall(card)
        name, tip = (unescape(value) for value in match)
        assert name == label and tip == help_text, label
        # 押すと小窓に出す (吹き出しの包みも、押下を止める onclick も無い)
        assert "tooltip" not in card and 'data-tip="' not in card
        assert "onclick" not in re.search(r'<button type="button"[^>]*>', card).group(0)
    # 数の下の 1 行 (入力を省けた時間 ≒ m 分) は残る
    assert "入力を省けた時間 ≒ 0 分" in by_label["手入力せずに取り込んだ明細"]
    assert len(INFO.findall(stats_part(body))) == len(HELP)


@pytest.mark.django_db
def test_card_without_help_has_no_info(logged_in, monkeypatch):
    plain = {"label": "作り物の数", "value": 3, "unit": "件", "note": "作り物の注", "icon": "box", "url": None}
    explained = {**plain, "label": "作り物の説明つきの数", "help": "作り物の数え方の 1 文です。", "url": reverse("core:home")}
    monkeypatch.setattr(views, "dashboard_stats", lambda: [plain, explained])

    by_label = cards(home(logged_in))

    assert "<button" not in by_label["作り物の数"]
    assert "data-stat-help" not in by_label["作り物の数"]
    assert "aria-label" not in by_label["作り物の数"]
    ((name, tip),) = INFO.findall(by_label["作り物の説明つきの数"])
    assert (unescape(name), unescape(tip)) == ("作り物の説明つきの数", "作り物の数え方の 1 文です。")


@pytest.mark.django_db
def test_info_stays_for_user_without_money(logged_in, settings):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}

    body = home(logged_in)
    by_label = cards(body)

    assert list(by_label) == list(HELP)
    for label in ("今月の支払の見込み", "今月の入金"):
        card = by_label[label]
        assert "—" in card, label  # 金額は「—」
        ((name, tip),) = INFO.findall(card)
        assert (unescape(name), unescape(tip)) == (label, HELP[label]), label
    assert len(INFO.findall(stats_part(body))) == len(HELP)
