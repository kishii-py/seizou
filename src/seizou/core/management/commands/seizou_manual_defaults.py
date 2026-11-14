"""標準の説明書の断片のうち、会社の置き場の manual/ に無い物を写す (版を上げた後に打つ)。既にある断片は上書きしない。

処理は seizou.core.manual_defaults に置き、この命令は結果を書くだけ。表を変えない。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.core import manual_defaults


class Command(BaseCommand):
    help = (
        "標準の説明書の断片 (画面ごとの「使い方」) のうち、置き場の manual/ に無い物を写す。版を上げた後に打つ。"
        "既にある断片 (自社で直した物と前の版の物) は上書きしない。"
    )

    # ほかの設定に誤りがあっても写せる (ファイルを写すだけ。控えの命令と同じ)
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="置き場の断片のうち、標準と字が違う物の名前も出す。")

    def handle(self, *args, **options):
        try:
            result = manual_defaults.copy_missing()
        except manual_defaults.ManualDefaultsError as exc:
            raise CommandError(str(exc)) from exc
        copied = result["copied"]
        different = result["different"]
        if copied:
            self.stdout.write(f"標準の説明書の断片を写しました: {len(copied)} 件 (置き場に無かった画面の分。{result['folder']})。")
        else:
            self.stdout.write(f"写した物はありません (標準の断片は全部、置き場にあります。{result['folder']})。")
        if different:
            self.stdout.write(
                f"置き場の断片のうち、標準と字が違う物: {len(different)} 件 (自社で直した物か、前の版の標準の物。上書きしていません)。"
                "標準の新しい文に替えたい断片は、置き場のファイルを消してから、もう一度この命令を打ちます。"
                + ("" if options["list"] else "名前は --list で出します。")
            )
            if options["list"]:
                for name in different:
                    self.stdout.write(f"  {name}")
