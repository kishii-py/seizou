## 何を直すか (背景・使用シーン・影響範囲)

(関係する Issue があれば番号を。決まりを実装した関数を変える PR は、先に Issue (決まりの提案か決まりの反例) で議論する = CONTRIBUTING.md)

## 確かめた事 (当てはまる物に印)

- [ ] `uv run pytest` が通る (決まりのテスト `tests/test_r*.py` を含む)
- [ ] `uv run ruff check .` と決まりの検査 (`DJANGO_SETTINGS_MODULE=tests.settings uv run python -m django seizou_check src/seizou`) が通る
- [ ] Django の部品が持つ表を変えていない (会社ごとの項目は拡張の表 `<表>Ext` に足す)。出した migration (`src/seizou/released_migrations.txt`) を消していない・名前を変えていない
- [ ] 数と金額を変える処理は `services` の関数を通している
- [ ] 決まりを実装した関数を足した・変えたときは、docstring に「訳」(何のためか・出所・変えてよい条件) を書いた (R22)
- [ ] 実在の会社名・人名・鍵の値を書いていない。画面に出る言葉は日本語
