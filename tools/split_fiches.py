"""Split a multi-page permit set into per-sheet 'Fiche' PDFs, named by the
sheet title found on each page. Demonstrates that a single combined set can be
fed to the app as the multi-document submission it really is.

  python tools/split_fiches.py <set.pdf> <out_folder>
"""

import os
import re
import sys

import fitz

TITLE_HINTS = [
    (r"main floor plan", "main_floor_plan"),
    (r"upper floor plan", "upper_floor_plan"),
    (r"window schedule", "window_schedule"),
    (r"typical stair detail|stair detail", "stair_detail"),
    (r"section a-?a|section a\b", "section"),
    (r"foundation plan", "foundation_plan"),
    (r"roof plan", "roof_plan"),
    (r"garage floor plan", "garage_plan"),
    (r"elevation", "elevation"),
    (r"site plan", "site_plan"),
]


def _name_for(text, page_no):
    low = text.lower()
    for pat, name in TITLE_HINTS:
        if re.search(pat, low):
            return name
    return "sheet_%02d" % page_no


def split(pdf_path, out_folder):
    os.makedirs(out_folder, exist_ok=True)
    doc = fitz.open(pdf_path)
    used = {}
    written = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        base = _name_for(text, i + 1)
        used[base] = used.get(base, 0) + 1
        suffix = "" if used[base] == 1 else "_%d" % used[base]
        name = "fiche_%02d_%s%s.pdf" % (i + 1, base, suffix)
        one = fitz.open()
        one.insert_pdf(doc, from_page=i, to_page=i)
        path = os.path.join(out_folder, name)
        one.save(path)
        written.append(name)
    return written


if __name__ == "__main__":
    pdf, folder = sys.argv[1], sys.argv[2]
    for n in split(pdf, folder):
        print("wrote", n)
