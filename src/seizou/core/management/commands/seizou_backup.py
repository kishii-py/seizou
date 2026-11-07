"""DB と上げたファイルと答えの YAML を、日時の名前のフォルダに控える (バックアップ)。

処理は seizou.core.backup に置き、この命令は引数を読んで結果を書くだけ。表を変えない。
.env (鍵) は控えず、その案内の 1 行を出す。
写した DB の確かめ (integrity_check) と会社の作り込みの写し (site/) と backup.json の sha256 は、この命令の道だけで行う (backup.take_backup の full)。
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from seizou.core import backup

DB_LABELS = {"sqlite": "SQLite", "postgresql": "PostgreSQL"}


class Command(BaseCommand):
    help = "データベースと上げたファイルと答えの YAML を、日時の名前のフォルダに控える (バックアップ)。.env (鍵) は控えない。"

    # 控えは、ほかの設定に誤りがあっても取れなければならない (05 の 4 節の禁じること)。
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--to", help="控えの親のフォルダ (既定は置き場の backups/)。")

    def handle(self, *args, **options):
        try:
            parent = Path(options["to"]) if options["to"] else backup.default_dir()
            result = backup.take_backup(parent, full=True)
        except backup.BackupError as exc:
            raise CommandError(str(exc)) from exc
        kind = DB_LABELS.get(result["database"], result["database"])
        answers = "有" if result["answers"] else "無"
        folder = result["folder"].resolve()
        self.stdout.write(
            f"控えを取りました: {folder} (DB: {kind}・上げたファイル: {result['media_files']} 個・answers.yaml: {answers})"
        )
        self.stdout.write(backup.ENV_NOTICE)
