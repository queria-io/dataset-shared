# /// script
# requires-python = ">=3.11"
# dependencies = ["queria"]
# ///
"""vocab/concepts.csv の約束を確かめる。

    uv run scripts/check-vocab.py

語彙は Queria の公開データセット（gif・schemaorg）から引く。

- gif_term は gif.vocab.term の同じ class にある語であること
- schema_org は schemaorg.vocab.property にある語であること
- 語彙の語がある行の concept は、GIF か schema.org の語を snake_case にしたものであること
- concept と aliases はそれぞれ重複せず、互いに重ならないこと
"""

import csv
import os
import re
import sys
from pathlib import Path

VOCAB = Path(__file__).resolve().parent.parent / "vocab"


def read(name: str) -> list[dict]:
    with (VOCAB / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def snake(term: str) -> str:
    # gif.vocab.term・schemaorg.vocab.property の snake_case_name と同じ規則（height_CMT → height_cmt）
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", term).lower()


def candidates(c: dict) -> set[str]:
    return {snake(t) for t in (c["gif_term"], c["schema_org"]) if t}


def problems(concepts: list[dict], gif: set[tuple[str, str]], schemaorg: set[str]) -> list[str]:
    names = [c["concept"] for c in concepts]
    aliases = [a for c in concepts for a in c["aliases"].split(";") if a]
    return [
        *(f"{c['concept']}: GIF の {c['gif_class']} に {c['gif_term']} が無い" for c in concepts if c["gif_term"] and (c["gif_class"], c["gif_term"]) not in gif),
        *(f"{c['concept']}: schema.org に {c['schema_org']} が無い" for c in concepts if c["schema_org"] and c["schema_org"] not in schemaorg),
        *(f"{c['concept']}: {' か '.join(sorted(candidates(c)))} にする" for c in concepts if candidates(c) and c["concept"] not in candidates(c)),
        *(f"{n}: concept が重複している" for n in sorted({n for n in names if names.count(n) > 1})),
        *(f"{a}: alias が重複している" for a in sorted({a for a in aliases if aliases.count(a) > 1})),
        *(f"{a}: alias が concept と重なっている" for a in sorted(set(aliases) & set(names))),
    ]


def vocabularies() -> tuple[set[tuple[str, str]], set[str]]:
    import queria

    con = queria.connect(token=os.environ.get("QUERIA_TOKEN") or None)
    gif = {tuple(r) for r in con.sql("SELECT class_name, term_en FROM gif.vocab.term").fetchall()}
    schemaorg = {r[0] for r in con.sql("SELECT name FROM schemaorg.vocab.property").fetchall()}
    return gif, schemaorg


if __name__ == "__main__":
    found = problems(read("concepts.csv"), *vocabularies())
    print("\n".join(found) or "ok", file=sys.stderr)
    sys.exit(1 if found else 0)
