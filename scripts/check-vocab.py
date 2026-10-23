"""vocab/concepts.csv の約束を確かめる。

    python3 scripts/check-vocab.py

- gif_term は vocab/gif_core.csv の同じ class にある語であること
- 語彙の語がある行の concept は、GIF か schema.org の語を snake_case にしたものであること
- concept と aliases はそれぞれ重複せず、互いに重ならないこと
"""

import csv
import re
import sys
from pathlib import Path

VOCAB = Path(__file__).resolve().parent.parent / "vocab"


def read(name: str) -> list[dict]:
    with (VOCAB / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def snake(term: str) -> str:
    # dataset-gif の vocab.term.snake_case_name と同じ規則（height_CMT → height_cmt）
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", term).lower()


def candidates(c: dict) -> set[str]:
    return {snake(t) for t in (c["gif_term"], c["schema_org"]) if t}


def problems(concepts: list[dict], gif: list[dict]) -> list[str]:
    known = {(g["class"], g["term_en"]) for g in gif}
    names = [c["concept"] for c in concepts]
    aliases = [a for c in concepts for a in c["aliases"].split(";") if a]
    return [
        *(f"{c['concept']}: GIF の {c['gif_class']} に {c['gif_term']} が無い" for c in concepts if c["gif_term"] and (c["gif_class"], c["gif_term"]) not in known),
        *(f"{c['concept']}: {' か '.join(sorted(candidates(c)))} にする" for c in concepts if candidates(c) and c["concept"] not in candidates(c)),
        *(f"{n}: concept が重複している" for n in sorted({n for n in names if names.count(n) > 1})),
        *(f"{a}: alias が重複している" for a in sorted({a for a in aliases if aliases.count(a) > 1})),
        *(f"{a}: alias が concept と重なっている" for a in sorted(set(aliases) & set(names))),
    ]


if __name__ == "__main__":
    found = problems(read("concepts.csv"), read("gif_core.csv"))
    print("\n".join(found) or "ok", file=sys.stderr)
    sys.exit(1 if found else 0)
