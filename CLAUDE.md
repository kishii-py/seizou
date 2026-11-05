# seizou の決まり (Claude Code と、ほかの AI の道具が読む)

このリポジトリは Built with Claude: Japan 2026 (2026-11-02 12:30 〜 2026-11-08 20:00 JST) の作品。町工場や中小の製造業が、1 つの命令で自社専用のシステムを持ち、部品マスターを中心に社内の情報を 1 つにつなぎ、同じ仕組みで会社どうしもつながる。Claude Code のプラグイン (`plugin/`) と、pip で入れる Django の部品 (`src/seizou/`) を置く。

## 独立

- このリポジトリは独立している。手元のほかのリポジトリや社内のコードを読まず、使わず、写さない。ここにある物はゼロから書く。
- 作り物のデータだけを使う。実在の会社名・人名・品番・図面を使わない。
- 社内の部品の名前、客先と取引先の名前、鍵の形を書かない (一覧はリポジトリの外にあり、push の前の検査が見る)。
- 鍵は `.env` だけ。設定ファイルと commit に鍵の値を書かない。

## 置き場

- `src/seizou/` Django の部品 (app 群)。`core` が土台で、ほかの app は core と、相手の `services.py` だけを呼ぶ。
- `plugin/` Claude Code のプラグイン (skills・hooks)。
- `examples/companies/` 2 社の作り物の答え (YAML)。
- `tests/` 決まりのテスト (`tests/test_r<番号>_<短い名前>.py`。RULES.md の番号と 1 対 1) と、ほかのテスト (`tests/test_<app>_*.py`)。
- `docs/nights/` 無人の仕事の記録。`docs/e2e/` 通しの確かめの記録。
- `scripts/` CSS を作る・検査の道具。
- python は uv の環境で動かす (`uv run python`・`uv run pytest`・`uv run manage.py ...`)。venv の外の python を使わない。

## 書き方の決まり (RULES.md の R13〜R19。決まりの検査が見る)

- 画面は `core` の土台 (`Base*View`) を継承する。
- 数と金額を変える処理は部品の `services` の関数を通す。画面から表に直接書かない。
- SQL を直接書かない (`raw`・`cursor` を使わない)。
- 部品の表を変えない。会社ごとの項目は拡張の表 (`<表>Ext`) に足す。
- 相手の会社への HTTP は `transaction.atomic` の外で送る。
- CSRF の見出しは土台のテンプレートの 1 か所にだけ書く。
- class 名を途中で組み立てない。
- 画面に出る言葉は日本語。識別子は英語。
- 画面の class の docstring に、ユースケースを「使う人: … / いつ: … / 何のために: … / 次にすること: …」の形で 1〜2 行書く (`seizou.core.screens.all_screens()` が `usecase` として拾い、MCP と AI チャットの `list_screens` が返す。`tests/test_core_screens.py` が全部の画面に揃っていることを見る)。

## git

- 履歴を書き換えない (rebase・squash・force-push を使わない)。
- commit は明示したパスだけ (`git add -A` と `git add .` を使わない)。commit の前に `git status` で、外の変更が混ざっていないかを見る。
- 作者は本人の GitHub。commit の末尾に `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` を付ける。
- commit の文に客先の話を書かない。feat と improvement の commit には、分かる範囲で背景・使用シーン・影響範囲を書く。
- main に入れてよいのは、テストと決まりの検査と禁止語の検査が通った枝だけ。通れば Claude が merge して push してよい。本人は朝に差分と決まりの一覧を見る。
- テストを消して通さない。決まりの検査を無効にしない。通らないテストを黙って skip にしない。

## 無人の仕事 (依頼書で動くとき)

- 触ってよいのは依頼書の「触ってよい物」だけ。1 本の仕事は 1 app。手元の commit まで。push しない。
- 仕様に無い物を足さない。迷ったら小さい方を選び、迷った事を記録に書く。
- 止まったら `docs/nights/<日付>/<題>.md` に 1 行書いて終わる。粘らない。部品に足りない物は要望として 1 行書き残す。
- 同じ app の model を 2 本が同時に変えない (migration の番号が重なる)。

## 言葉

- 書く物は日本語が主 (README の冒頭の英語 3 行は例外)。
- 「壊れない」と書かない。「お金と数の決まりを破る変更は本番の前に止まる」と書く。
- 中小企業共通 EDI は「準拠」と書かず、「項目を元にした」と書く。
- 「外注」と書かない。「協力企業」と書く (取引先の種類の表示名も「協力企業」。`tests/test_wording.py` が止める)。
- 「釦」と書かない。「ボタン」と書く (`tests/test_wording.py` が止める)。
