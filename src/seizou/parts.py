"""存在する部品 (Django の app) の module の一覧。

会社のプロジェクトの settings と試験用の settings は、この一覧から INSTALLED_APPS を作る。
部品を足すレーンは、自分の app をこの一覧の末尾に足す (足すだけ。並びは core が先)。
"""

PARTS = [
    "seizou.core",
    "seizou.masters",
    "seizou.orders",
    "seizou.connect",
    "seizou.shipping",
    "seizou.purchasing",
]
