"""会社の置き場の説明書の断片 (manual/) を、標準の組にそろえる (版を上げた後に打つ)。

無い断片を写し、会社が手を入れていない断片 (前の版の標準と同じ中身の物) を新しい標準に替える。会社が直した断片は替えない。
処理は seizou.core.manual_defaults に置き、この命令は結果を書くだけ。表を変えない。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.core import manual_defaults


class Command(BaseCommand):
    help = (
        "置き場の説明書の断片 (画面ごとの「使い方」) を、標準の組にそろえる。版を上げた後に打つ。"
        "無い断片を写し、手を入れていない断片 (前の版の標準と同じ中身の物) を新しい標準に替える。自社で直した断片は替えない。"
    )

    # ほかの設定に誤りがあっても打てる (ファイルを写すだけ。控えの命令と同じ)
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="替えた断片と、自社で直した断片 (替えなかった物) の名前も出す。")

    def handle(self, *args, **options):
        try:
            result = manual_defaults.sync()
        except manual_defaults.ManualDefaultsError as exc:
            raise CommandError(str(exc)) from exc
        copied, replaced, kept = result["copied"], result["replaced"], result["kept"]
        show = options["list"]
        if copied:
            self.stdout.write(f"標準の説明書の断片を写しました: {len(copied)} 件 (置き場に無かった画面の分。{result['folder']})。")
        else:
            self.stdout.write(f"写した物はありません (標準の断片は全部、置き場にあります。{result['folder']})。")
        if replaced:
            self.stdout.write(f"前の版の標準のままだった断片を、新しい標準に替えました: {len(replaced)} 件 (手を入れていない断片だけ)。")
            if show:
                for name in replaced:
                    self.stdout.write(f"  替えた: {name}")
        if kept:
            self.stdout.write(
                f"自社で直した断片: {len(kept)} 件 (どの版の標準とも中身が違う物。替えていません)。"
                "標準の新しい文に替えたい断片は、置き場のファイルを消してから、もう一度この命令を打ちます。"
                + ("" if show else "名前は --list で出します。")
            )
            if show:
                for name in kept:
                    self.stdout.write(f"  替えていない: {name}")
