"""Re-download the third-party validation data that is deliberately not committed.

The repository tracks only source, rules, tests, docs and tiny sample JSONs. The
real drawings and code corpora below belong to their respective publishers and
are freely downloadable, so they are fetched on demand instead of vendored.

  python tools/fetch_data.py

Sources and licences remain with the original publishers:
  - IfcOpenHouse IFC4 model            IfcOpenShell project (GitHub)
  - QCAD example drawings (DXF)        QCAD project (GPL examples, GitHub)
  - Ontario permit application form    Government of Ontario (public form)
  - Calgary new-home sample drawings   City of Calgary (public sample set)
  - BC Building Code JSON (NBC 2020)   Government of British Columbia (open data)
  - CODE-ACCORD corpus                 ACCORD project (published research corpus)
"""

import os
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = [
    # (destination relative to repo root, url)
    ("data/real/IfcOpenHouse_IFC4.ifc",
     "https://raw.githubusercontent.com/aothms/IfcOpenHouse/master/IfcOpenHouse_IFC4.ifc"),
    ("data/real/flange.dxf",
     "https://raw.githubusercontent.com/qcad/qcad/master/examples/flange.dxf"),
    ("data/real/example00.dxf",
     "https://raw.githubusercontent.com/qcad/qcad/master/examples/example00.dxf"),
    ("data/real/entities.dxf",
     "https://raw.githubusercontent.com/qcad/qcad/master/examples/entities.dxf"),
    ("data/real/ontario_permit_form.pdf",
     "https://files.ontario.ca/mmah_1/mmah-building-development-application-for-a-permit-to-construct-or-demolish-2014-en-2021-11-01.pdf"),
    ("data/real/calgary_new_home_sample.pdf",
     "https://www.calgary.ca/content/dam/www/pda/pd/documents/carls/sample-drawings/DP-BP-new-home-sample-drawings.pdf"),
    ("data/bc_code/BuildingCode.json",
     "https://raw.githubusercontent.com/bcgov/BC-Building-Code/develop/BuildingCode.json"),
    ("data/code_accord/entities_all.csv",
     "https://raw.githubusercontent.com/Accord-Project/CODE-ACCORD/main/annotated_data/entities/all.csv"),
    ("data/code_accord/relations_all.csv",
     "https://raw.githubusercontent.com/Accord-Project/CODE-ACCORD/main/annotated_data/relations/all.csv"),
]


def fetch(dest, url):
    path = os.path.join(BASE, dest)
    if os.path.exists(path):
        print("  skip (exists): %s" % dest)
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PermitCheck/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as fh:
            fh.write(r.read())
        print("  ok  %8.1f KB  %s" % (os.path.getsize(path) / 1024.0, dest))
        return True
    except Exception as exc:
        print("  FAIL %s (%s)" % (dest, exc))
        return False


def main():
    print("Fetching third-party validation data ...")
    ok = sum(fetch(d, u) for d, u in FILES)
    print("%d/%d available." % (ok, len(FILES)))
    print("\nNext, regenerate the local fixtures:")
    print("  python tools/make_samples.py      # demo IFC + DXF + PDF submission")
    print("  python tools/make_floorplan.py    # scan fixtures for the benchmarks")
    print("  python tools/make_logo.py         # documentation header logo")
    return 0 if ok == len(FILES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
