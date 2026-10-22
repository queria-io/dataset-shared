# vocab

列名と列の日本語名（`title`）を国の語彙に揃えるための表。

- `gif_core.csv`: デジタル庁の政府相互運用性フレームワーク（GIF）のコアデータモデル（DMD）を 1 語 1 行にしたもの。`scripts/fetch-gif-vocab.py` で作る。手で直さない
- `columns.csv`: Queria の列名と GIF・IMI の語の対応表。手で書く

## 列名の決め方

1. `columns.csv` にある概念なら、その `column` と `title` を使う
2. 無ければ `gif_core.csv` を引き、GIF の英語名を snake_case にした名前と、GIF の項目名を `title` に使う。`columns.csv` に行を足す
3. GIF にも無ければ自由に決めてよい。複数のデータセットで使うなら `columns.csv` に `gif_term` を空にして足す

`aliases` は以前使っていた名前と、使ってはいけない同義の名前。既存のデータセットにある
`aliases` の列は、旧列を残したまま `column` の名前で新列を足し、カラム説明で非推奨を
書いてから一定期間後に落とす。

`entity` は `*.table.yml` の `semantic: { role: entity, name: ... }` に書く名前。データセット
横断の結合はこの名前でつながるので、列名を変えても結合グラフは変わらない。

## 出典

- GIF: [digital-go-jp/GIF](https://github.com/digital-go-jp/GIF)（デジタル庁）。リポジトリの
  LICENSE は CC0 1.0、DMD の XSD 内の表記は CC BY 4.0。どちらでも足りるよう出典をここに書く。
  `version` 列がタグ
- IMI: [共通語彙基盤 コア語彙 2.4.2](https://imi.go.jp/core/core242/)（CC0 1.0）。`imi_term` は
  語の名前で、URI は `http://imi.go.jp/ns/core/rdf#` に続けたもの

GIF の語の URI は v2.4 時点で暫定（`http://xxx/xxx/gif/xxx`）なので、ここでは
`gif_class` と `gif_term` の組で指す。

## 検査

```bash
python3 scripts/check-vocab.py
```

`gif_term` が `gif_core.csv` にあること、`column` がその snake_case であること、`aliases` が
重複せず `column` と重ならないことを確かめる。`vocab/` を変える PR で CI が回す。
