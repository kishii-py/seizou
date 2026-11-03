"""R17: 相手の会社への HTTP は transaction.atomic の外で送る。

決まりの検査 (seizou.core.rules.check) を一時の置き場 (tmp_path) の見本に直に当てる。Django の設定に頼らない。
"""

from pathlib import Path

import pytest

import seizou
from seizou.core.rules import check, r17_http_outside_atomic

SEIZOU_DIR = Path(seizou.__file__).parent
RULES_MD = Path(__file__).parent.parent / "RULES.md"


def _rules_md_text(code: str) -> str:
    for line in RULES_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {code} |"):
            return line.split("|")[2].strip().replace("`", "")
    raise AssertionError(f"RULES.md に {code} の行が無い")


def _services(tmp_path: Path, text: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "services.py"
    path.write_text(text, encoding="utf-8")
    return path


def _line_of(path: Path, text: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(f"{path} に {text} が無い")


def _r17(result):
    return [(v.path, v.line) for v in result.violations if v.code == "R17"]


def test_http_inside_with_atomic_is_reported(tmp_path):
    path = _services(
        tmp_path,
        "import requests\n"
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    with transaction.atomic():\n"
        "        requests.post(url, timeout=10)\n"
        "\n"
        "\n"
        "def send_chain(url):\n"
        "    with transaction.atomic(using='default'):\n"
        "        answer = (\n"
        "            requests\n"
        "            .post(url, timeout=10)\n"
        "        )\n"
        "    return answer\n",
    )

    result = check([path])

    assert _r17(result) == [(str(path), 7), (str(path), 14)]
    assert result.scanned == 1


@pytest.mark.parametrize(
    ("decorator", "imports", "call"),
    [
        ("@transaction.atomic", "import httpx\nfrom django.db import transaction\n", "httpx.get(url)"),
        ("@transaction.atomic()", "import requests\nfrom django.db import transaction\n", "requests.get(url)"),
        ("@atomic", "import httpx\nfrom django.db.transaction import atomic\n", "httpx.get(url)"),
    ],
)
def test_http_inside_decorated_function_is_reported(tmp_path, decorator, imports, call):
    path = _services(tmp_path, f"{imports}\n\n{decorator}\ndef send(url):\n    note = 1\n    return {call}\n")

    result = check([path])

    assert _r17(result) == [(str(path), _line_of(path, call))]


def test_imported_atomic_and_urlopen_forms(tmp_path):
    path = _services(
        tmp_path,
        "import urllib.request\n"
        "from urllib.request import urlopen\n"
        "\n"
        "from django.db.transaction import atomic\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    with atomic():\n"
        "        urlopen(url)\n"
        "    with atomic(savepoint=False):\n"
        "        urllib.request.urlopen(url)\n",
    )

    result = check([path])

    assert _r17(result) == [(str(path), 9), (str(path), 11)]


def test_call_to_same_module_function_is_followed_one_step(tmp_path):
    inside = _services(
        tmp_path / "inside",
        "import requests\n"
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def _deliver(url):\n"
        "    return requests.post(url, timeout=10)\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    with transaction.atomic():\n"
        "        _deliver(url)\n",
    )
    outside = _services(
        tmp_path / "outside",
        "import requests\n"
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def _deliver(url):\n"
        "    return requests.post(url, timeout=10)\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    with transaction.atomic():\n"
        "        pass\n"
        "    _deliver(url)\n",
    )

    assert _r17(check([inside])) == [(str(inside), 11)]
    assert _r17(check([outside])) == []


def test_http_after_the_block_is_not_reported(tmp_path):
    path = _services(
        tmp_path,
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def send_order(order, url):\n"
        "    import requests\n"
        "\n"
        "    with transaction.atomic():\n"
        "        order.note = '送っています'\n"
        "\n"
        "    if url:\n"
        "        # ここは atomic の外。\n"
        "        try:\n"
        "            answer = requests.post(url, data=b'{}', timeout=10)\n"
        "        except requests.RequestException:\n"
        "            answer = None\n"
        "\n"
        "    with transaction.atomic():\n"
        "        order.note = str(answer)\n",
    )

    result = check([path])

    assert _r17(result) == []
    assert result.scanned == 1


def test_unsure_forms_are_not_reported(tmp_path):
    session = _services(
        tmp_path / "session",
        "import requests\n"
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    session = requests.Session()\n"
        "    with transaction.atomic():\n"
        "        session.post(url)\n"
        "        requests.Session().post(url)\n",
    )
    nested = _services(
        tmp_path / "nested",
        "import requests\n"
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    with transaction.atomic():\n"
        "        def later():\n"
        "            return requests.post(url)\n"
        "        callback = lambda: requests.post(url)\n"
        "    return later, callback\n",
    )
    no_import = _services(
        tmp_path / "no_import",
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def send(requests, url):\n"
        "    with transaction.atomic():\n"
        "        requests.post(url)\n",
    )
    atomic_not_imported = _services(
        tmp_path / "atomic_not_imported",
        "import requests\n"
        "\n"
        "\n"
        "def send(atomic, url):\n"
        "    with atomic():\n"
        "        requests.post(url)\n",
    )
    two_steps = _services(
        tmp_path / "two_steps",
        "import requests\n"
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def _post(url):\n"
        "    return requests.post(url)\n"
        "\n"
        "\n"
        "def _deliver(url):\n"
        "    return _post(url)\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    with transaction.atomic():\n"
        "        _deliver(url)\n",
    )

    for path in (session, nested, no_import, atomic_not_imported, two_steps):
        assert _r17(check([path])) == [], path


def test_connect_services_is_clean():
    services = SEIZOU_DIR / "connect" / "services.py"

    result = check([services])

    assert _r17(result) == []
    assert "requests.post(" in services.read_text(encoding="utf-8")


def test_ignore_mark_silences_r17(tmp_path):
    body = (
        "import requests\n"
        "from django.db import transaction\n"
        "\n"
        "\n"
        "def send(url):\n"
        "    with transaction.atomic():\n"
        "        requests.post(url)  # seizou: ignore[{code}] 作り物の見本\n"
    )
    silenced = _services(tmp_path / "silenced", body.format(code="R17"))
    other_number = _services(tmp_path / "other_number", body.format(code="R14"))

    result = check([silenced])

    assert result.violations == ()
    assert result.silenced == 1

    result = check([other_number])

    assert _r17(result) == [(str(other_number), 7)]
    assert result.silenced == 0


def test_message_is_the_rules_md_text():
    assert r17_http_outside_atomic.MESSAGE == _rules_md_text("R17")
    assert "atomic" in r17_http_outside_atomic.FIX
