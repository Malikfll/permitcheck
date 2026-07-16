import re
import sys
from collections import Counter

import fitz


def inspect(path):
    doc = fitz.open(path)
    pg = doc[0]
    words = pg.get_text("words")
    texts = [w[4] for w in words]
    print("== %s == tokens: %d" % (path.split("\\")[-1], len(words)))
    caps = [t for t in texts if re.fullmatch(r"[A-Z][A-Z./&-]{2,}", t)]
    print("room-name candidates:", [t for t, _ in Counter(caps).most_common(40)])
    dims = [t for t in texts if re.search(r"\d+'", t) or re.fullmatch(r"\d{2,4}", t)]
    print("dimension-like tokens (%d):" % len(dims), dims[:30])
    rooms = [t for t in texts if re.search(r"(ROOM|OFFICE|STAIR|TOILET|LOBBY|CORR|MECH|"
             r"ELEC|STOR|KITCHEN|LOUNGE|CLOSET|VEST|CONF)", t, re.I)]
    print("functional-space tokens:", Counter(rooms).most_common(20))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        inspect(p)
        print()
