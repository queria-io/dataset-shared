"""vocab/columns.csv の約束を確かめる。

    python3 scripts/check-vocab.py

- gif_term は vocab/gif_core.csv の同じ class にある語であること
- gif_term がある行の column は、その語を snake_case にしたものであること
- aliases は重複せず、どの column とも重ならないこと
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


def problems(columns: list[dict], gif: list[dict]) -> list[str]:
    known = {(g["class"], g["term_en"]) for g in gif}
    names = [c["column"] for c in columns]
    aliases = [a for c in columns for a in c["aliases"].split(";") if a]
    with_term = [c for c in columns if c["gif_term"]]
    return [
        *(f"{c['column']}: GIF の {c['gif_class']} に {c['gif_term']} が無い" for c in with_term if (c["gif_class"], c["gif_term"]) not in known),
        *(f"{c['column']}: {c['gif_term']} なら {snake(c['gif_term'])}" for c in with_term if snake(c["gif_term"]) != c["column"]),
        *(f"{n}: column が重複している" for n in sorted({n for n in names if names.count(n) > 1})),
        *(f"{a}: alias が重複している" for a in sorted({a for a in aliases if aliases.count(a) > 1})),
        *(f"{a}: alias が column と重なっている" for a in sorted(set(aliases) & set(names))),
    ]


if __name__ == "__main__":
    found = problems(read("columns.csv"), read("gif_core.csv"))
    print("\n".join(found) or "ok", file=sys.stderr)
    sys.exit(1 if found else 0)
