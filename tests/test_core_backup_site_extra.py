"""命令 seizou_backup の控えの site/ に checks.py と .claude/ が入るか (依頼書 N387 の 2)。

出所は精査 Q4 (2026-11-10) のつまずく所 6。置き場ごと失って seizou new で作り直すと、会社のポカよけ (checks.py) と
Claude Code に .env・DB・控えを読ませない設定 (.claude/settings.json) が消える見込みだった。
置き場と命令の回し方は tests/test_core_backup.py と tests/test_core_backup_keep.py の物を借りる。中身は作り物。
"""

import pytest

from seizou.core import backup
from tests import test_core_backup
from tests.test_core_backup import folders, run, sqlite_only
from tests.test_core_backup_keep import manifest_of, put_site_files

# 控えの試験の置き場 (BASE_DIR = 一時のフォルダの site/)
site = test_core_backup.site

CHECKS_TEXT = "# 作り物の会社のポカよけ\n"
CLAUDE_TEXT = '{"permissions": {"deny": ["Read(/作り物.env)"]}}\n'


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_command_backup_copies_checks_and_claude_settings(site):
    put_site_files(site)
    (site / "checks.py").write_text(CHECKS_TEXT, encoding="utf-8")
    (site / ".claude").mkdir()
    (site / ".claude" / "settings.json").write_text(CLAUDE_TEXT, encoding="utf-8")

    run("seizou_backup")

    made = folders(site / "backups")
    assert len(made) == 1
    saved = made[0] / backup.SITE_DIR
    assert (saved / "checks.py").read_text(encoding="utf-8") == CHECKS_TEXT
    assert (saved / ".claude" / "settings.json").read_text(encoding="utf-8") == CLAUDE_TEXT
    hashes = manifest_of(made[0])["sha256"]
    assert "site/checks.py" in hashes
    assert "site/.claude/settings.json" in hashes
    assert not (saved / ".env").exists()
