"""注文書の見張り。フォルダに置いた注文書を古い順に読み取り、届いた注文に入口「読み取り」の行で並べる (常駐か 1 周)・見張りが動いているかを見る (--status)・止める合図を置く (--stop)。

処理は seizou.intake.watch に置き、この命令は引数を読んで結果を書くだけ。表を書かない (書くのは intake.services)。
画面でないので権限の判定は通らない (置き場を預かる人が立てる)。出すのはファイルの名前・届いた注文の番号・取引先の名前・
読み取った差出人の字・日本語の理由だけ (鍵の値・注文書の中身・読み取りの JSON は出さない)。
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from seizou.intake import services, watch

MESSAGE_NO_KEY_WARNING = (
    f"鍵 ({services.API_KEY_ENV}) が無いので、置いた注文書は読めずに failed/ へ動きます。"
    ".env に鍵を置いてから見張ってください。"
)
MESSAGE_NOT_RUNNING = "見張りは動いていません。"


class Command(BaseCommand):
    help = (
        "注文書の見張り: フォルダに置いた注文書 (PDF・PNG・JPEG) を古い順に読み取り、届いた注文に入口「読み取り」の行で並べる。"
        "読んだ物は done/、読めなかった物は failed/ (理由は .txt) へ動かす。止めるときは --stop か Ctrl+C。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "folder", nargs="?", help="見張るフォルダ (省けば settings の SEIZOU の intake_watch_folder)。"
        )
        parser.add_argument(
            "--interval", type=int, default=watch.DEFAULT_INTERVAL, help="見回りの間の秒 (1 以上。既定 10)。"
        )
        parser.add_argument("--user", default="admin", help="読み取った人にする利用者のログイン ID (既定 admin)。")
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument(
            "--once", action="store_true", help="1 周だけ見て終わる (cron と試験用。見張りの記録を書かない)。"
        )
        actions.add_argument("--status", action="store_true", help="見張りが動いているかを見る。")
        actions.add_argument("--stop", action="store_true", help="見張りに止める合図を置く (次の見回りで止まる)。")

    def handle(self, *args, **options):
        try:
            if options["status"]:
                self._status()
            elif options["stop"]:
                self._stop()
            else:
                self._watch(options)
        except watch.WatchError as exc:
            raise CommandError(str(exc)) from exc

    def _watch(self, options):
        interval = options["interval"]
        if interval < 1:
            raise CommandError("--interval は 1 以上の秒にしてください。")
        folder = self._folder(options["folder"])
        user = self._user(options["user"])
        if options["once"]:
            if not services.is_available():
                self.stderr.write(MESSAGE_NO_KEY_WARNING)
            rows = watch.once(folder, user)
            for row in rows:
                self._write_row(row)
            counts = {key: sum(1 for row in rows if row["result"] == key) for key in ("inbox", "same", "failed")}
            self.stdout.write(
                f"1 周見ました: 読んだ {counts['inbox']} 件・同じ中身 {counts['same']} 件・読めなかった {counts['failed']} 件"
            )
            return
        watch.ensure_idle()
        self.stdout.write(
            f"見張りを始めました: {folder} ({interval} 秒ごと。止めるときは manage.py seizou_intake_watch --stop か Ctrl+C)"
        )
        if not services.is_available():
            self.stderr.write(MESSAGE_NO_KEY_WARNING)
        try:
            watch.run(folder, user, interval, self._write_row)
        except KeyboardInterrupt:
            pass
        self.stdout.write(f"見張りを止めました: {folder}")

    def _folder(self, given):
        raw = given or watch.default_folder()
        if not raw:
            raise CommandError(
                "見張るフォルダがありません。命令の引数か、答えの YAML の settings の intake_watch_folder で渡してください。"
            )
        folder = Path(raw).expanduser()
        if not folder.is_dir():
            raise CommandError(f"見張るフォルダがありません: {folder}")
        return folder

    def _user(self, login):
        model = get_user_model()
        try:
            return model._default_manager.get_by_natural_key(login)
        except model.DoesNotExist:
            raise CommandError(f"利用者 {login} がいません。--user でログイン ID を渡してください。") from None

    def _write_row(self, row):
        name = row["name"]
        inbox = row["inbox"]
        if row["result"] == "inbox":
            if inbox.partner_id:
                self.stdout.write(f"読んだ: {name} → 届いた注文 {inbox.pk} (取引先 {inbox.partner.name})")
            else:
                sender = inbox.sender_name or "差出人の名前なし"
                self.stdout.write(f"読んだ: {name} → 届いた注文 {inbox.pk} (差出人の確認待ち: {sender})")
        elif row["result"] == "same":
            self.stdout.write(f"同じ中身: {name} → 届いた注文 {inbox.pk} に入っています")
        else:
            moved = f"{watch.FAILED_DIR}/{row['moved'].name}" if row["moved"] is not None else "動かせませんでした"
            self.stdout.write(f"読めなかった: {name} → {moved} ({row['reason']})")

    def _status(self):
        current = watch.status()
        if current is None:
            self.stdout.write(MESSAGE_NOT_RUNNING)
        elif current["running"]:
            self.stdout.write(
                f"動いている: {current.get('folder', '')} (pid {current['pid']}・{current.get('interval', '')} 秒ごと・"
                f"始めた日時 {watch.status_time(current.get('started_at', ''))}・最後の見回り {watch.status_time(current.get('seen_at', ''))})"
            )
        else:
            self.stdout.write(
                f"止まっている (記録が古い): {current.get('folder', '')} "
                f"(pid {current['pid']}・最後の見回り {watch.status_time(current.get('seen_at', ''))})。もう一度立てると置き直します。"
            )

    def _stop(self):
        current = watch.read_record()
        if current is None or not watch.request_stop():
            self.stdout.write(MESSAGE_NOT_RUNNING)
            return
        self.stdout.write(f"止める合図を置きました。次の見回り ({current.get('interval', '')} 秒以内) で止まります。")
