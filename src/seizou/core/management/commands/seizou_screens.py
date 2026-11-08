"""画面の目録を出す (画面の目録と使い方の設計の決め)。説明書の命令と左のメニューの確かめが使う。"""

from django.core.management.base import BaseCommand

from seizou.core.screens import all_screens


class Command(BaseCommand):
    help = "土台を継承した画面の名前と 1 文と URL を一覧で出す"

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=("text", "tsv"), default="text")

    def handle(self, *args, **options):
        screens = all_screens()
        if options["format"] == "tsv":
            for s in screens:
                self.stdout.write("\t".join([s.app, s.view, s.name, s.help, s.url_name, s.path]))
            return
        if not screens:
            self.stdout.write("画面はまだありません。")
            return
        for s in screens:
            self.stdout.write(f"{s.name}  ({s.app} {s.view}  {s.url_name}  {s.path})")
            if s.help:
                self.stdout.write(f"    {s.help}")
