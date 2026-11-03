"""控え (seizou_backup のフォルダ) から、DB と上げたファイルを戻す (復元)。

戻す前に今の物を <日時>-before-restore の名前で控える (戻しの戻し)。--list で控えの一覧を出す。
確認の問いに yes と答えたときか、--yes のときだけ戻す。端末でないときの問いは、空の答えと同じに扱う。
処理は seizou.core.backup に置き、この命令は引数・問い・一覧・結果を書くだけ。
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from seizou.core import backup

MEGABYTE = 1024 * 1024


class Command(BaseCommand):
    help = (
        "控え (seizou_backup のフォルダ) からデータベースと上げたファイルを戻す。"
        "戻す前に今の物を <日時>-before-restore に控える。--list で控えの一覧。"
    )

    # 戻しは、ほかの設定に誤りがあっても動かなければならない (05 の 4 節の禁じること)。
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("backup", nargs="?", help="戻す控え (控えのフォルダの道か、控えの置き場の中の名前)。")
        parser.add_argument("--list", action="store_true", help="控えの一覧を出して終わる。")
        parser.add_argument("--dir", help="控えの置き場 (既定は置き場の backups/)。")
        parser.add_argument("--yes", action="store_true", help="確認の問いを飛ばして戻す。")

    def handle(self, *args, **options):
        try:
            parent = Path(options["dir"]) if options["dir"] else backup.default_dir()
        except backup.BackupError as exc:
            raise CommandError(str(exc)) from exc

        if options["list"]:
            rows = backup.list_backups(parent)
            if not rows:
                self.stdout.write(f"控えはまだありません: {parent}")
                return
            for row in rows:
                size = row["size"] / MEGABYTE
                self.stdout.write(
                    f"{row['name']}  {row['created_at']}  {row['database']}  seizou {row['seizou_version']}  {size:.1f} MB"
                )
            return

        name = options["backup"]
        if not name:
            raise CommandError("戻す控えを指定してください (一覧は seizou_restore --list)。")
        try:
            folder = backup.resolve_folder(name, parent)
            manifest = backup.read_manifest(folder)
        except backup.BackupError as exc:
            raise CommandError(str(exc)) from exc

        if not options["yes"]:
            media_root = getattr(settings, "MEDIA_ROOT", "") or ""
            self.stdout.write(
                f"{folder} の控え ({manifest.get('created_at', '')}) を戻します。"
                f"今のデータベースと上げたファイル ({media_root}) は置き換わります "
                f"(戻す前の物は {parent} に <日時>{backup.RETREAT_SUFFIX} の名前で控えます)。"
                "サーバー (runserver / gunicorn) を止めてから進めてください。"
            )
            try:
                answer = input("本当に戻しますか? (yes と打つと進む): ")
            except EOFError:
                answer = ""
            if answer.strip().lower() != "yes":
                self.stdout.write("やめました。何も変えていません。")
                return

        try:
            result = backup.restore_backup(folder, retreat_parent=parent)
        except backup.BackupError as exc:
            raise CommandError(str(exc)) from exc

        retreat = result["retreat"]
        self.stdout.write(
            f"戻しました: {folder}。戻す前の物は {retreat} にあります "
            f"(元に戻すなら manage.py seizou_restore {retreat})。"
        )
        saved_version = str(result["manifest"].get("seizou_version") or "")
        version = backup.current_version()
        if saved_version != version:
            self.stdout.write(f"控えの seizou の版 ({saved_version}) と今の版 ({version}) が違います。")
        self.stdout.write("次にすること: manage.py migrate → manage.py seizou_doctor → サーバーを立て直す。")
