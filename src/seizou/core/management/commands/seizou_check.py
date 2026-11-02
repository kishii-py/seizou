"""決まりの検査を CI と手元で回す管理コマンド (05 の 4 節の「CI と命令」)。

違反ごとに 1 行 (番号・ファイルと行・文・直し方) を出し、最後に合計の行を必ず出す。
違反が 1 件でもあるか、走査したファイルが 0 本のときは 0 以外で終わる。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.core.rules import check, default_paths


class Command(BaseCommand):
    help = "決まりの検査を回す。違反があるか、走査したファイルが 0 本なら 0 以外で終わる。"

    # 検査の命令を、ほかの設定の誤りで止まる作りにしない (05 の 4 節の禁じること)。
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "paths",
            nargs="*",
            help="検査するファイルかフォルダ。無ければ部品の置き場と BASE_DIR (BASE_DIR が無ければ部品の置き場だけ)。",
        )

    def handle(self, *args, **options):
        result = check(options["paths"] or default_paths())
        for v in result.violations:
            self.stdout.write(f"{v.code} {v.path}:{v.line} {v.message} 直し方: {v.fix}")
        count = len(result.violations)
        self.stdout.write(f"合計: 違反 {count} 件、黙らせた {result.silenced} 件、走査 {result.scanned} 本")
        if result.scanned == 0:
            raise CommandError("走査したファイルが 0 本です。検査するパスを確かめてください。")
        if count:
            raise CommandError(f"決まりの違反が {count} 件あります。")
