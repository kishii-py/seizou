"""黙って既定の値を返していた例外の受けが、記録 (logger の warning) に 1 行を残すことのテスト (依頼書 N371)。

返す値は前と同じであることも見る (動きは変えない)。記録に例外の中身 (AI の引数・利用者の入力を含みうる字) を書かないことも見る。
ファイルと絵は試験の一時のフォルダと記憶の中の作り物。本物の API は呼ばない。
"""

import io
import logging

from PIL import Image

from seizou.assistant import tools
from seizou.intake import watch
from seizou.masters import services as masters_services

SECRET_TEXT = "作り物の中身-記録に出さない"


def _warnings(caplog, name):
    return [record for record in caplog.records if record.name == name and record.levelno == logging.WARNING]


def test_run_tool_unexpected_error_logs_one_line_without_contents(monkeypatch, caplog):
    def broken():
        raise RuntimeError(SECRET_TEXT)

    monkeypatch.setitem(tools.FUNCTIONS, "list_screens", broken)
    with caplog.at_level(logging.WARNING, logger="seizou.assistant.tools"):
        outcome = tools.run_tool("list_screens", {})

    assert outcome == {"content": "list_screens の結果を読めませんでした。", "is_error": True, "urls": []}
    lines = _warnings(caplog, "seizou.assistant.tools")
    assert len(lines) == 1
    assert "list_screens" in lines[0].getMessage()
    assert "RuntimeError" in lines[0].getMessage()
    assert SECRET_TEXT not in caplog.text


def test_watch_handle_logs_when_done_move_fails(monkeypatch, tmp_path, caplog):
    path = tmp_path / "order.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    inbox = object()
    monkeypatch.setattr(watch.services, "extract_to_inbox", lambda file, user: (inbox, True))

    def cannot_move(target, folder):
        raise PermissionError(13, "動かせない (作り物)")

    monkeypatch.setattr(watch, "_move", cannot_move)
    with caplog.at_level(logging.WARNING, logger="seizou.intake.watch"):
        row = watch.handle(path, None)

    assert row["result"] == "inbox"
    assert row["inbox"] is inbox
    assert row["moved"] is None
    assert path.exists()
    lines = _warnings(caplog, "seizou.intake.watch")
    assert len(lines) == 1
    assert "order.pdf" in lines[0].getMessage()
    assert "done/" in lines[0].getMessage()


def test_later_pages_logs_when_page_cannot_be_drawn(monkeypatch, caplog):
    buffer = io.BytesIO()
    first, second = Image.new("L", (8, 8), 255), Image.new("L", (8, 8), 0)
    first.save(buffer, format="TIFF", save_all=True, append_images=[second])
    buffer.seek(0)

    def broken_png(page):
        raise OSError("作り物の壊れたページ")

    monkeypatch.setattr(masters_services, "_page_png", broken_png)
    with Image.open(buffer) as image, caplog.at_level(logging.WARNING, logger="seizou.masters.services"):
        pages = masters_services._later_pages(image)

    assert pages == []
    lines = _warnings(caplog, "seizou.masters.services")
    assert len(lines) == 1
    assert "2 ページ目" in lines[0].getMessage()
