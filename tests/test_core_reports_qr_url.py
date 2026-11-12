"""帳票の QR の頭を決める関数 core.reports.qr_url (本体の決め 11-11 14:2x と 11-12 13:1x。現品票・納品書・出荷ラベルが呼ぶ)。

- (a) 設定の URL の頭 (mail の site_url) があれば、その頭を付けた字と空の字を返す (開いている住所に依らない)。
- (b) 頭が空で、127.0.0.1 か localhost で開いた時は、開いている住所の字と、その住所の名前 (ポートを除く) を返す。
- (c) 頭が空で、社内の番地 (例 192.168.1.10:8001) で開いた時は、開いている住所の字と空の字を返す。
RequestFactory で request を作り、関数を直に呼ぶ (画面は通さない)。設定の値は画面の上書きの表も読むので db を使う。
"""

import pytest
from django.test import RequestFactory

from seizou.core import reports

SITE = "https://seizou.example.jp"
PATH = "/shipping/deliverynotes/7/"
LAN = "192.168.1.10:8001"


def _request(host):
    return RequestFactory().get("/", HTTP_HOST=host)


def test_local_only_hosts_are_loopback_names():
    assert reports.LOCAL_ONLY_HOSTS == ("127.0.0.1", "localhost")


# --- (a) 設定の URL の頭があれば、その頭を付けた字と空の字 ---


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["127.0.0.1:8001", "localhost:8001", LAN])
def test_site_url_heads_qr_text(settings, host):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "192.168.1.10"]
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    assert reports.qr_url(_request(host), PATH) == (SITE + PATH, "")


# --- (b) 頭が空で、手元だけの住所で開いた時は、開いている住所の字と、その名前 ---


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("host", "name"),
    [("127.0.0.1:8001", "127.0.0.1"), ("localhost:8001", "localhost"), ("127.0.0.1", "127.0.0.1"), ("localhost", "localhost")],
)
def test_local_host_without_site_url_returns_host_name(host, name):
    assert reports.qr_url(_request(host), PATH) == (f"http://{host}{PATH}", name)


# --- (c) 頭が空で、社内の番地で開いた時は、開いている住所の字と空の字 ---


@pytest.mark.django_db
@pytest.mark.parametrize("host", [LAN, "testserver"])
def test_other_host_without_site_url_returns_empty_name(settings, host):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "192.168.1.10"]
    assert reports.qr_url(_request(host), PATH) == (f"http://{host}{PATH}", "")
