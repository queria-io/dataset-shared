# vocab

列名と列の日本語名（`title`）を決めるための概念の表。名前の基準はデータの種類で選び、
名前が違っても同じ概念の列はこの表で結ぶ。

- `concepts.csv`: 1 行 1 概念。Queria の概念 ID（`concept`）と、schema.org・GIF・IMI の語、
  実際に使われている別名を並べる。手で書く
- `gif_core.csv`: デジタル庁の政府相互運用性フレームワーク（GIF）のコアデータモデル（DMD）を
  1 語 1 行にしたもの。`scripts/fetch-gif-vocab.py` で作る。手で直さない。dataset-gif の公開後に外す

## 名前の基準

- 一般のデータ（地図・施設・海外由来など）: schema.org の語を snake_case にする
- 日本の公的データ（自治体・国の統計や台帳）: GIF の英語名を snake_case にする。ただし GIF の
  語が schema.org の語に修飾を足しただけのとき（`latitudeOfRepresentativePoint`）は短い方を使う
- 日本語名（`title`）はどちらでも GIF の項目名、無ければ IMI の語

`concept` は日本の公的データで使う名前でもある。一般のデータでの名前は `schema_org` を
snake_case にしたもの（無ければ `concept`）。

## 列名の決め方

1. `concepts.csv` にある概念なら、上の基準でその名前と `title` を使う
2. 無ければ基準の語彙（schema.org か GIF）から探して snake_case にし、`concepts.csv` に行を足す
3. どの語彙にも無ければ自由に決めてよい。複数のデータセットで使うなら語彙の欄を空にして足す

`aliases` は同じ概念を指す、ほかの名前。基準の名前と違う既存の列は、無理に改名しなくてよい。
`lat` や `pref_code` のような省略形は、旧列を残したまま基準の名前で新列を足し、カラム説明で
非推奨を書いてから一定期間後に落とす。

`entity` は `*.table.yml` の `semantic: { role: entity, name: ... }` に書く名前。データセット
横断の結合はこの名前でつながるので、列名が違っても結合グラフは変わらない。

## 出典

- GIF: [digital-go-jp/GIF](https://github.com/digital-go-jp/GIF)（デジタル庁）。リポジトリの
  LICENSE は CC0 1.0、DMD の XSD 内の表記は CC BY 4.0。どちらでも足りるよう出典をここに書く。
  `version` 列がタグ
- schema.org: [schema.org](https://schema.org/)（CC BY-SA 3.0）。`schema_org` はプロパティ名
- IMI: [共通語彙基盤 コア語彙 2.4.2](https://imi.go.jp/core/core242/)（CC0 1.0）。`imi_term` は
  語の名前で、URI は `http://imi.go.jp/ns/core/rdf#` に続けたもの

GIF の語の URI は v2.4 時点で暫定（`http://xxx/xxx/gif/xxx`）なので、ここでは
`gif_class` と `gif_term` の組で指す。

## 検査

```bash
python3 scripts/check-vocab.py
```

`gif_term` が `gif_core.csv` にあること、`concept` が GIF か schema.org の語の snake_case であること、
`aliases` が重複せず `concept` と重ならないことを確かめる。`vocab/` を変える PR で CI が回す。
