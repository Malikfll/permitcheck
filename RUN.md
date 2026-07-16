![PermitCheck](assets/PermitCheck_logo.png)

# PermitCheck - How to run

All commands are run from the project root:
`C:\Users\fallm\Downloads\SolutionDev`

---

## 1. One-time setup

**a) Python 3.9 or newer** (check with `python --version`).

**b) Install the extraction-stack dependencies** (computer vision, PDF, CAD, OCR):

```
pip install -r requirements.txt
```

This installs: opencv-python, numpy, ezdxf, pdfplumber, PyMuPDF, pytesseract.

> The core compliance engine, API, UI, reports and audit use only the Python standard
> library. The packages above are needed for reading real drawings (CV/PDF/CAD) and OCR.

**c) Install the Tesseract OCR engine** (only needed for scanned-image OCR):

```
winget install UB-Mannheim.TesseractOCR
```

**d) Confirm the stack is ready:**

```
python -c "import cv2, fitz, ezdxf, pdfplumber, pytesseract; print('extraction stack OK')"
```

If this prints `extraction stack OK`, everything is installed. If `python` reports a missing
module, run the commands above using the same interpreter, or use the full path to the Python
that has the packages.

---

## 2. Run the application (web UI - the main way to use it)

```
python -m permitcheck.cli serve
```

Then open **http://127.0.0.1:8742** in a browser.

In the UI you can:

- **Upload your own documents** (one or many PDF/image/IFC/DXF sheets) and click the button,
  or pick a built-in sample permit, then run the deterministic compliance check.
- See the four-category verdicts, per-rule detail, code articles and source documents.
- Fill in any missing data (the dynamic manual-entry form).
- Record human review decisions.
- View the background-optimized review plan.
- Open the annotated-document viewer (drawings with detections overlaid).
- Export results (CSV / HTML-PDF / JSON / BCF) and watch the hash-chained audit trail.

To stop the server: press `Ctrl + C` in the terminal.

*(Optional custom port: `python -m permitcheck.cli serve --port 9000`.)*

---

## 3. Run from the command line (no browser)

**Check a single structured application:**
```
python -m permitcheck.cli check data\applications\APP-2026-0142_duplex_issues.json
python -m permitcheck.cli check data\applications\APP-2026-0142_duplex_issues.json --lang fr
```

**Check a raw-file submission folder (IFC + DXF + PDF with a manifest):**
```
python -m permitcheck.cli check data\submissions\APP-2026-0201
```

**Check a multi-document set (several separate "Fiches" in one folder):**
```
python -m permitcheck.cli check-set --folder data\submissions\calgary_fiches
```

**Check a scanned floor-plan image directly:**
```
python -m permitcheck.cli check-scan data\scans\SCAN-2026-0302.png --title-block data\scans\SCAN-2026-0302.truth.json --verbose
```

**Analyze a real plan sheet (schedule extraction, occupant load, missing-info request):**
```
python -m permitcheck.cli analyze-plan "C:\path\to\your\drawing.pdf"
```

**Analyze one or many sheets and see what is missing for a full verdict:**
```
python -m permitcheck.cli analyze sheet1.pdf sheet2.pdf sheet3.pdf
```

**Measure geometry in a CAD file or scanned image:**
```
python -m permitcheck.cli measure data\real\flange.dxf
python -m permitcheck.cli measure scan.png --px-per-mm 2.0
```

**List the machine-readable ruleset:**
```
python -m permitcheck.cli rules
```

---

## 4. Accuracy benchmarks (reproducible evidence)

```
python -m permitcheck.cli benchmark            # verdict-logic accuracy (simple / complex)
python -m permitcheck.cli benchmark-geometry   # CV dimensional accuracy on real CAD drawings
python -m permitcheck.cli benchmark-semantic   # scanned-drawing -> verdict accuracy
```

---

## 5. Digitalized-code ingestion

```
python -m permitcheck.codes_nbc               # ingest the real NBC (open BC edition) into rules
python -m permitcheck.codes_accord            # ingest the CODE-ACCORD corpus into rule stubs
```

---

## 6. Run the automated tests

```
python -m unittest discover tests -v
```

Expected result: `Ran 82 tests ... OK`.

---

## 7. Regenerate the documents (branded PDFs)

```
python tools\make_logo.py                                    # (re)build the header logo
python tools\make_pdf.py                                     # DOCUMENTATION.md  -> DOCUMENTATION.pdf
python tools\make_pdf.py SUBMISSION_ANSWERS.md SUBMISSION_ANSWERS.pdf
```

To use the official PermitCheck logo instead of the recreated one, replace
`assets\PermitCheck_logo.png` with your file and re-run the two `make_pdf.py` commands.

---

## 8. Quick smoke test (verifies the whole install in ~2 minutes)

```
python -c "import cv2, fitz, ezdxf, pdfplumber, pytesseract; print('stack OK')"
python -m unittest discover tests
python -m permitcheck.cli benchmark
python -m permitcheck.cli serve
```

If the tests pass, the benchmark prints 100% on both rule classes, and the server starts on
port 8742, the application is running correctly.

---

## Troubleshooting

- **`No module named cv2` (or fitz/ezdxf) when serving:** the terminal's `python` is not the one
  where the packages were installed. Re-run `pip install -r requirements.txt` with that same
  `python`, or call the interpreter that has them (the one where step 1d prints `OK`).
- **OCR features fail / "Tesseract required":** run the winget install in step 1c and reopen the
  terminal so the Tesseract path is picked up.
- **Port already in use:** start with a different port, e.g. `--port 9000`.
- **Web page loads but shows "API not reachable":** you opened the HTML file directly. Always use
  `python -m permitcheck.cli serve` and open the `http://127.0.0.1:8742` URL.
