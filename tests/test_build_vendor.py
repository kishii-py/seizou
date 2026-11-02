"""同梱物の版の一覧 (scripts/vendor.toml) の試験。"""

import hashlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR_TOML = ROOT / "scripts" / "vendor.toml"
REQUIRED_KEYS = ("name", "version", "url", "license", "sha256")


def load_items():
    with VENDOR_TOML.open("rb") as f:
        return tomllib.load(f)["item"]


def test_vendor_toml_items_have_required_keys():
    items = load_items()
    assert items
    for item in items:
        for key in REQUIRED_KEYS:
            assert item.get(key), f"{item.get('name')} に {key} がありません"
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), item["name"]


def test_vendor_toml_urls_contain_version():
    for item in load_items():
        assert item["version"] in item["url"], f"{item['name']} の URL に版がありません"


def test_vendor_toml_names_are_unique():
    names = [item["name"] for item in load_items()]
    assert len(names) == len(set(names))


def test_vendored_static_files_match_sha256():
    for item in load_items():
        if "path" not in item:
            continue
        path = ROOT / item["path"]
        assert path.is_file(), f"{item['path']} がありません"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item[
            "path"
        ]
