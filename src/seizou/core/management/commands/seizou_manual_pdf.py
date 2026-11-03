"""使い方 (画面ごとの説明書) を、表紙と目次とページ番号の付いた PDF の冊子にして書き出す (05 の 10 節)。

既定の書き出し先は会社の置き場の manual/使い方.pdf。冊子にする物 (manual/) が無ければ止まる。
書き出し先のフォルダは作らない。PDF の部品 (WeasyPrint) が使えなければ、その 1 行を出して止まる。
置き場の断片は読むだけ。表に書かない (会社の行が無いときだけ、services.company が既定の 1 行を作る)。
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from seizou.core import manual, reports


class Command(BaseCommand):
    help = "使い方 (画面ごとの説明書) を、表紙と目次とページ番号の付いた PDF の冊子にして書き出す"

    def add_arguments(self, parser):
        parser.add_argument("--out", help="書き出し先のパス (既定は会社の置き場の manual/使い方.pdf)")

    def handle(self, *args, **options):
        folder = manual.manual_dir()
        if folder is None or not folder.is_dir():
            raise CommandError("説明書の置き場 (manual/) がありません。先に /seizou:manual で説明書を書いてください。")
        out = Path(options["out"]) if options["out"] else folder / manual.BOOKLET_FILENAME
        if not out.parent.is_dir():
            raise CommandError(f"書き出し先のフォルダがありません: {out.parent}")
        context = manual.booklet_context()
        try:
            data = reports.render_pdf(manual.BOOKLET_TEMPLATE, context)
        except reports.PdfUnavailable as exc:
            raise CommandError(str(exc)) from exc
        out.write_bytes(data)
        sections = context["sections"]
        written = sum(1 for section in sections if section["html"] is not None)
        self.stdout.write(f"使い方の冊子を書きました: {out.resolve()} ({written} / {len(sections)} 画面に説明書)")
