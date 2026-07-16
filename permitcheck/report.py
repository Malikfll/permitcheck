"""Report generation - export compliance results (essential outcome).

Formats: JSON (machine-to-machine / open API), CSV (Excel-compatible) and a
self-contained bilingual HTML report suitable for print-to-PDF. A production
system would add native PDF and BCF 3.0 issue export for BIM workflows.
"""

import csv
import html
import io

L10N = {
    "en": {
        "report_title": "Building Permit Compliance Report",
        "MEETS": "Meets", "DOES_NOT_MEET": "Does not meet",
        "INFO_NOT_AVAILABLE": "Information not available", "UNCERTAIN": "Uncertain",
        "rule": "Rule", "reference": "Code reference", "verdict": "Verdict",
        "element": "Element", "observed": "Observed", "limit": "Limit",
        "reason": "Notes", "classification": "Classification",
        "part": "Applicable code part", "summary": "Summary", "overall": "Overall result",
        "not_applicable": "Not applicable", "reviews": "Human review decisions",
        "reviewer": "Reviewer", "decision": "Decision", "comment": "Comment",
        "generated": "Generated", "engine": "Engine", "input_hash": "Input fingerprint",
        "application": "Application",
    },
    "fr": {
        "report_title": "Rapport de conformité - demande de permis de construction",
        "MEETS": "Conforme", "DOES_NOT_MEET": "Non conforme",
        "INFO_NOT_AVAILABLE": "Information non disponible", "UNCERTAIN": "Incertain",
        "rule": "Règle", "reference": "Référence au code", "verdict": "Verdict",
        "element": "Élément", "observed": "Valeur observée", "limit": "Limite",
        "reason": "Notes", "classification": "Classification",
        "part": "Partie du code applicable", "summary": "Sommaire", "overall": "Résultat global",
        "not_applicable": "Non applicable", "reviews": "Décisions de l'examen humain",
        "reviewer": "Examinateur", "decision": "Décision", "comment": "Commentaire",
        "generated": "Généré le", "engine": "Moteur", "input_hash": "Empreinte des données",
        "application": "Demande",
    },
}

VERDICT_COLORS = {
    "MEETS": "#1a7f37", "DOES_NOT_MEET": "#c62828",
    "UNCERTAIN": "#e07b00", "INFO_NOT_AVAILABLE": "#5c6bc0",
}


def _rows(run: dict, lang: str):
    t = L10N[lang]
    for r in run["results"]:
        if not r["applicable"]:
            yield [r["rule_id"], r["reference"], r["title"][lang],
                   t["not_applicable"], "", "", "", r.get("skip_reason", "")]
            continue
        if not r["instances"]:
            yield [r["rule_id"], r["reference"], r["title"][lang],
                   t[r["verdict"]], "", "", "", r.get("note", "")]
        for inst in r["instances"]:
            if inst.get("exception_applied"):
                yield [r["rule_id"], r["reference"], r["title"][lang],
                       t["MEETS"], inst["element"], "", "", "exception applied"]
                continue
            if not inst["checks"]:
                yield [r["rule_id"], r["reference"], r["title"][lang],
                       t[inst["verdict"]], inst["element"], "", "", ""]
            for c in inst["checks"]:
                unit = c.get("unit") or ""
                yield [
                    r["rule_id"], r["reference"], r["title"][lang],
                    t[c["verdict"]], inst["element"],
                    "" if c.get("observed") is None else "%s %s" % (c["observed"], unit),
                    "" if c.get("limit") is None else "%s %s" % (c["limit"], unit),
                    c.get("reason", "") or "",
                ]


def to_csv(run: dict, lang: str = "en") -> str:
    t = L10N[lang]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([t["rule"], t["reference"], "Title", t["verdict"],
                t["element"], t["observed"], t["limit"], t["reason"]])
    for row in _rows(run, lang):
        w.writerow(row)
    return buf.getvalue()


def to_html(run: dict, lang: str = "en") -> str:
    t = L10N[lang]
    e = html.escape

    def badge(verdict_key):
        color = VERDICT_COLORS.get(verdict_key, "#666")
        return ('<span style="background:%s;color:#fff;padding:2px 8px;'
                'border-radius:10px;font-size:12px">%s</span>'
                % (color, e(t.get(verdict_key, verdict_key))))

    parts = [
        "<!DOCTYPE html><html lang='%s'><head><meta charset='utf-8'>" % lang,
        "<title>%s</title>" % e(t["report_title"]),
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#222}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;"
        "padding:6px 10px;font-size:13px;text-align:left}th{background:#f4f4f4}"
        "h1{font-size:22px}.meta{color:#555;font-size:13px}</style></head><body>",
        "<h1>%s</h1>" % e(t["report_title"]),
        "<p class='meta'>%s: %s - %s | %s: %s | %s PermitCheck %s (ruleset %s) | %s: %s</p>" % (
            e(t["application"]), e(str(run["application"]["id"])),
            e(str(run["application"]["municipality"])),
            e(t["generated"]), e(run["timestamp"]),
            e(t["engine"]), e(run["engine_version"]), e(run["ruleset_version"]),
            e(t["input_hash"]), e(run["input_hash"][:23] + "…"),
        ),
        "<p><strong>%s:</strong> %s %s &nbsp; <strong>%s:</strong> %s</p>" % (
            e(t["part"]), e(t.get("part", "Part")), run["classification"]["part"],
            e(t["overall"]), badge(run["overall"]),
        ),
        "<p><strong>%s:</strong> " % e(t["summary"]) + " &nbsp; ".join(
            "%s %d" % (badge(k), v) for k, v in run["summary"].items()) + "</p>",
        "<table><tr>" + "".join("<th>%s</th>" % e(h) for h in [
            t["rule"], t["reference"], "Title", t["verdict"], t["element"],
            t["observed"], t["limit"], t["reason"]]) + "</tr>",
    ]
    verdict_reverse = {v: k for k, v in t.items() if k in VERDICT_COLORS}
    for row in _rows(run, lang):
        cells = []
        for i, cell in enumerate(row):
            if i == 3:
                key = verdict_reverse.get(cell)
                cells.append("<td>%s</td>" % (badge(key) if key else e(str(cell))))
            else:
                cells.append("<td>%s</td>" % e(str(cell)))
        parts.append("<tr>%s</tr>" % "".join(cells))
    parts.append("</table>")

    if run.get("reviews"):
        parts.append("<h2>%s</h2><table><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>" % (
            e(t["reviews"]), e(t["rule"]), e(t["reviewer"]), e(t["decision"]), e(t["comment"])))
        for rev in run["reviews"]:
            parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                e(rev["rule_id"]), e(rev["reviewer"]),
                e(t.get(rev["decision"], rev["decision"])), e(rev.get("comment", ""))))
        parts.append("</table>")

    parts.append("</body></html>")
    return "".join(parts)
