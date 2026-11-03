"""作り物の会社の部署 4 つとアカウント 3 つ (02 の 7 節)。core の demo から呼ぶ (load)。

入れるのは services だけ (R13)。2 回入れても重ならない (同じ名前の部署・同じログイン ID は飛ばす。上書きしない)。
合言葉は 2 社とも同じ作り物の字 (作り物の置き場だけで使う。鍵ではない)。システム管理者 (admin) は触らない。
"""

from __future__ import annotations

from django.contrib.auth import get_user_model

from . import services
from .models import Department

#: 作り物のアカウントの合言葉 (英小文字と数字と - だけ)
DEMO_PASSWORD = "seizou-demo-1103"

#: (部署, 開ける部品, 既定の権限, 並び)
DEPARTMENTS = (
    ("製造", ("workorders", "progress", "masters"), ("workorders",), 1),
    ("受注と出荷", ("orders", "intake", "connect", "shipping", "purchasing", "masters"), ("orders", "shipping"), 2),
    ("経理", ("billing", "orders", "shipping", "masters"), ("billing",), 3),
    ("総務", ("masters",), ("master_edit",), 4),
)

#: (ログイン ID, 部署, 権限, 金額の閲覧)。genba は部署の既定 (指示書) を外した形
ACCOUNTS = (
    ("genba", "製造", (), False),
    ("jimu", "受注と出荷", ("slip_edit",), False),
    ("keiri", "経理", ("billing",), True),
)


def load(user) -> dict:
    """部署とアカウントを入れる。返す物は {"部署": {"入れた": n, "飛ばした": m}, "アカウント": {...}}。"""
    result = {"部署": {"入れた": 0, "飛ばした": 0}, "アカウント": {"入れた": 0, "飛ばした": 0}}
    for name, apps, permissions, sort_order in DEPARTMENTS:
        if Department.objects.filter(name=name).exists():
            result["部署"]["飛ばした"] += 1
            continue
        services.create_department(name, list(apps), list(permissions), user, sort_order=sort_order)
        result["部署"]["入れた"] += 1
    User = get_user_model()
    for login_id, department, permissions, see_money in ACCOUNTS:
        if User._default_manager.filter(username=login_id).exists():
            result["アカウント"]["飛ばした"] += 1
            continue
        services.issue_account(
            login_id,
            DEMO_PASSWORD,
            [Department.objects.get(name=department)],
            list(permissions),
            {"see_money": see_money},
            user,
        )
        result["アカウント"]["入れた"] += 1
    return result
