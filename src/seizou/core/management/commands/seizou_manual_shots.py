"""使い方 (画面ごとの説明書) の絵を、手元で動いている会社の画面から撮って manual/images/ に置く (画面の目録と使い方の設計の決め。開発の記録 docs/nights/20261105/N168.md)。

撮るのは画面の目録 (seizou.core.screens.all_screens) の全部。引数の無い道はそのまま開き、<int:pk> の道は画面の表の最初の 1 件で開く。
使い方の画面そのもの・1 件も無い・道の引数が pk でない・部品の表が無い・開いて 200 でない画面は飛ばし、理由を 1 行ずつ出す。
絵の名前は断片と同じ manual/images/<断片の名前>.png (既にあれば上書き)。ブラウザは headless の Chromium 1 つで、窓 (幅 --width・高さ 800) の最初の 1 画面を撮る (--full-page を付けたときだけ画面の全体)。小窓は開かない。
Playwright は製品の依存に入れない。無ければ入れ方の 1 行を出して止まる。表に書かない (読むだけ)。パスワードは引数に取らない (環境変数 SEIZOU_SHOTS_PASSWORD)。
"""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.urls import reverse

from seizou.core import manual
from seizou.core.screens import all_screens

#: Playwright が無いときの 1 行
PLAYWRIGHT_MISSING = "Playwright が要ります: `uv pip install playwright` と `playwright install chromium` (開発の環境では `uv sync --group dev`)"

#: ログインのパスワードを渡す環境変数の名前 (引数には取らない)
PASSWORD_ENV = "SEIZOU_SHOTS_PASSWORD"

#: 道の引数 (<int:pk> など)
PATH_ARG = re.compile(r"<(?:\w+:)?\w+>")

#: 撮らない画面 (使い方の画面そのもの。全部の画面の箱を縦に並べた長い 1 枚で、手引きの絵にならない。印刷の 1 枚は同じ道の ?print=1、冊子は目録の外)
MANUAL_SCREENS = ("core:manual",)


def shot_targets(only: str | None = None) -> list[tuple[str, str, str | None, str]]:
    """撮る画面の並び (目録の並びのまま)。1 画面 = (断片の名前, 画面の名前, 開く道 か None, 飛ばす理由 か 空の字)。

    only は URL の名前 (例 orders:order_list)。その 1 画面だけを返す (目録に無ければ空の並び)。
    """
    targets = []
    for screen in all_screens():
        if only and screen.url_name != only:
            continue
        name = manual.fragment_name(screen.url_name)
        if screen.url_name in MANUAL_SCREENS:
            targets.append((name, screen.name, None, "使い方の画面そのもの"))
            continue
        args = PATH_ARG.findall(screen.path)
        if not args:
            targets.append((name, screen.name, reverse(screen.url_name), ""))
            continue
        if args != ["<int:pk>"]:
            targets.append((name, screen.name, None, "道の引数が pk でない"))
            continue
        model = getattr(manual.view_class_of(screen), "model", None)
        if model is None:
            targets.append((name, screen.name, None, "画面の表が分からない"))
            continue
        try:
            first = model._default_manager.order_by("pk").first()
        except DatabaseError:
            targets.append((name, screen.name, None, "部品の表が無い"))
            continue
        if first is None:
            targets.append((name, screen.name, None, "1 件も無い"))
            continue
        targets.append((name, screen.name, reverse(screen.url_name, kwargs={"pk": first.pk}), ""))
    return targets


class Command(BaseCommand):
    help = "使い方 (画面ごとの説明書) の絵を、手元で動いている会社の画面から撮って manual/images/ に置く (既定は最初の 1 画面。--full-page で画面の全体)"

    def add_arguments(self, parser):
        parser.add_argument("--base", default="http://127.0.0.1:8000", help="手元で動いている会社の URL (既定 http://127.0.0.1:8000)")
        parser.add_argument("--width", type=int, default=1280, help="画面の幅 (既定 1280)")
        parser.add_argument("--full-page", action="store_true", help="画面の全体を撮る (既定は窓の高さ 800 の最初の 1 画面)")
        parser.add_argument("--only", help="1 画面だけ撮る (URL の名前。例 orders:order_list)")
        parser.add_argument("--out", help="絵の置き場 (既定は会社の置き場の manual/images/)")
        parser.add_argument("--user", help=f"ログインの ID (自動ログインの無い会社だけ。パスワードは環境変数 {PASSWORD_ENV})")

    def handle(self, *args, **options):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CommandError(PLAYWRIGHT_MISSING) from exc
        folder = manual.manual_dir()
        out = Path(options["out"]) if options["out"] else (folder / "images" if folder is not None else None)
        if out is None or not out.is_dir():
            raise CommandError(f"絵の置き場のフォルダがありません: {out if out is not None else 'manual/images/'}")
        targets = shot_targets(options["only"])
        if options["only"] and not targets:
            raise CommandError(f"画面の目録に無い名前です: {options['only']}")
        base = options["base"].rstrip("/")
        taken = skipped = 0
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": options["width"], "height": 800})
                self.log_in(page, base, options["user"])
                for name, title, path, reason in targets:
                    if path is None:
                        skipped += 1
                        self.stdout.write(f"飛ばした: {title} ({name}。{reason})")
                        continue
                    response = page.goto(base + path, wait_until="networkidle")
                    status = response.status if response is not None else 0
                    if status != 200:
                        skipped += 1
                        self.stdout.write(f"飛ばした: {title} ({name}。開くと {status})")
                        continue
                    page.screenshot(path=str(out / f"{name}.png"), full_page=options["full_page"])
                    taken += 1
            finally:
                browser.close()
        self.stdout.write(f"撮った {taken}・飛ばした {skipped} ({out.resolve()})")

    def log_in(self, page, base: str, user: str | None) -> None:
        """会社の頭の道を開き、ログインの画面に移されたときだけ --user と環境変数のパスワードで入る。

        開発機の自動ログイン (.env の SEIZOU_DEV_AUTO_LOGIN=1) の会社は移されないので、そのまま撮る。
        """
        login_path = reverse("core:login")
        page.goto(base + "/", wait_until="networkidle")
        if urlparse(page.url).path != login_path:
            return
        password = os.environ.get(PASSWORD_ENV, "")
        if not user or not password:
            raise CommandError(f"ログインが要ります: --user と環境変数 {PASSWORD_ENV} を渡してください (開発機は .env の SEIZOU_DEV_AUTO_LOGIN=1 で要りません)。")
        page.fill("input[name=username]", user)
        page.fill("input[name=password]", password)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        if urlparse(page.url).path == login_path:
            raise CommandError(f"ログインできませんでした: --user と {PASSWORD_ENV} を確かめてください。")
