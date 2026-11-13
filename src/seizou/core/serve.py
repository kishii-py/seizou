"""社内の PC でサーバーを起こす (manage.py seizou_serve の中身)。

本人の決め 11-14: 社内の PC 1 台を LAN の中だけで使う形も本番の形にする。nginx を入れなくても動くように、
この命令が画面 (Django) と、画面の CSS と JS (静的ファイル) の両方を返す。

- サーバーは waitress (Windows・Mac・Linux で動く。gunicorn は Windows で動かない)。
- CSS と JS は whitenoise で返す。ファイルは Django の探し方 (staticfiles の finders) の場所から直に読むので、
  collectstatic は要らない。画面の `?v=` の印 (core/templatetags/seizou_static.py の file_stamp) も同じ場所のファイルから
  作るので、印と返す中身が食い違わない。
- 包むのはこの命令の中だけで、settings.py の MIDDLEWARE には足さない (前に作った置き場の settings.py を直さずに使えるため)。
- 版を上げた後は、命令を起こし直す (ファイルの一覧は起こした時に 1 回だけ読む)。

表に書く処理は無い。
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders

#: 待ち受ける番地の既定 (社内のほかの端末から開けるように、PC の全部の番地で待つ)
DEFAULT_HOST = "0.0.0.0"  # noqa: S104
#: 同時にさばく要求の数の既定 (waitress の threads。VPS の手順の gunicorn は 4)
DEFAULT_THREADS = 8
#: LAN の中だけで http で開く置き場の印 (settings の名前。.env の SEIZOU_LAN_HTTP=1)
LAN_HTTP_SETTING = "SEIZOU_LAN_HTTP"


class ServeError(Exception):
    """起こせないときの、日本語の文つきの誤り。"""


def lan_http() -> bool:
    """LAN の中だけで http で開く置き場か (settings の SEIZOU_LAN_HTTP が真)。"""
    return bool(getattr(settings, LAN_HTTP_SETTING, False))


def default_port() -> int:
    """既定のポート (settings の SEIZOU の port = 答えの YAML の port)。無ければ ServeError。"""
    seizou = getattr(settings, "SEIZOU", None)
    port = seizou.get("port") if isinstance(seizou, dict) else None
    if isinstance(port, int):
        return port
    raise ServeError("会社のポートが settings の SEIZOU に無いので、--port でポートを指定してください。")


def static_roots() -> list[tuple[Path, str]]:
    """静的ファイルの場所と、その場所の前に付く道 (STATICFILES_DIRS の prefix。無ければ空の字) の並び。

    Django の探し方と同じ順 (先の場所の同じ名前のファイルが勝つ)。同じ場所は 1 回だけ。
    """
    roots = []
    for finder in finders.get_finders():
        for _path, storage in finder.list(None):
            location = getattr(storage, "location", None)
            if not location:
                continue
            item = (Path(location), getattr(storage, "prefix", None) or "")
            if item not in roots:
                roots.append(item)
    return roots


def static_prefix() -> str:
    """静的ファイルの URL の頭 (settings の STATIC_URL。前後に / を付けた形)。"""
    return "/" + str(settings.STATIC_URL).strip("/") + "/"


def build_app():
    """画面 (Django) と静的ファイルの両方を返す WSGI の app。"""
    from django.core.wsgi import get_wsgi_application
    from whitenoise import WhiteNoise

    # max_age=0: ブラウザに毎回確かめさせる (VPS の nginx の no-cache と同じ扱い。版を上げた後に古い CSS を残さない)
    app = WhiteNoise(get_wsgi_application(), max_age=0)
    base = static_prefix()
    # whitenoise は後から足した場所が勝つので、Django の順 (先が勝つ) の逆から足す
    for root, prefix in reversed(static_roots()):
        if root.is_dir():
            app.add_files(str(root), prefix=base + (prefix.strip("/") + "/" if prefix else ""))
    return app


def run(host: str, port: int, threads: int) -> None:
    """待ち受けを始める (止めるまで戻らない)。

    waitress の serve() でなく create_server() で起こす。serve() は logging.basicConfig() を呼ぶので、
    置き場の記録 (settings の LOGGING) の行が 2 回ずつ出るため (11-14 に実際に起こして見た)。
    """
    import waitress

    waitress.create_server(build_app(), host=host, port=port, threads=threads, ident="seizou").run()
