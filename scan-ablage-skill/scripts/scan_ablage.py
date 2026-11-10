#!/usr/bin/env python3
"""scan_ablage.py - Helfer fuer den scan-ablage-skill.

Unterbefehle:
  latest   <ordner>                      neuesten Scan (PDF/Bild) im Ordner finden
  ocr      <eingabe> <ausgabe.pdf>       OCR (deu+eng) mit ocrmypdf, Ergebnis durchsuchbar
  pages    <pdf>                         Text pro Seite + vorgeschlagene Dokumentgrenzen (JSON)
  split    <pdf> <plan.json> <zielordner> PDF laut Plan in Einzeldokumente zerlegen
  extract  <pdf> [--mieter mieter.csv]   Kerndaten auslesen (Magistrat, Mietvertrag, Mieter ...)
  log      <extract.json> <logordner>    Ergebnis an Mietsoftware-CSV und Assistenz-Liste anhaengen

Alle Ausgaben, die Claude weiterverarbeitet, sind JSON auf stdout.
Benoetigt: python3, pypdf; fuer OCR zusaetzlich ocrmypdf + tesseract (deu, eng).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCAN_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic"}


# --------------------------------------------------------------------------- helpers

def out(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def page_texts(pdf: Path) -> list[str]:
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(pdf)).pages]


def slug(text: str, maxlen: int = 40) -> str:
    repl = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
    for k, v in repl.items():
        text = text.replace(k, v)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text[:maxlen].rstrip("-") or "unbekannt"


def safe_filename(name: str) -> str:
    stem = Path(name).stem
    parts = [slug(p, 60) for p in stem.split("_") if p.strip()]
    return "_".join(parts) + ".pdf"


# --------------------------------------------------------------------------- latest

def cmd_latest(args) -> None:
    folder = Path(args.folder).expanduser()
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SCAN_EXT]
    if not files:
        out({"error": f"keine Scans in {folder}"})
        sys.exit(1)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    newest = files[0]
    out({
        "latest": str(newest),
        "modified": dt.datetime.fromtimestamp(newest.stat().st_mtime).isoformat(timespec="seconds"),
        "others": [str(p) for p in files[1:6]],
    })


# --------------------------------------------------------------------------- ocr

def cmd_ocr(args) -> None:
    src, dst = Path(args.input), Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ocrmypdf"):
        out({"error": "ocrmypdf nicht installiert (pip install ocrmypdf; apt install tesseract-ocr-deu)"})
        sys.exit(1)
    cmd = ["ocrmypdf", "-l", args.lang, "--rotate-pages", "--deskew", "--skip-text",
           "--output-type", "pdf", "--optimize", "1"]
    if src.suffix.lower() != ".pdf":
        cmd += ["--image-dpi", "300"]
    cmd += [str(src), str(dst)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode not in (0, 6):  # 6 = enthaelt bereits Text
        out({"error": "ocrmypdf fehlgeschlagen", "code": res.returncode, "stderr": res.stderr[-2000:]})
        sys.exit(1)
    if res.returncode == 6:
        shutil.copyfile(src, dst)
    out({"ocr_pdf": str(dst), "pages": len(page_texts(dst))})


# --------------------------------------------------------------------------- pages

START_HINTS = [
    r"\bSeite\s*1\s*(von|/)\s*\d+",
    r"\bMietvertrag\b",
    r"\bBescheid\b",
    r"\bRechnung\b",
    r"\bSehr geehrte",
    r"\bBetreff\b",
    r"\bMagistrat\b",
    r"\bGesch(ae|ä)ftszahl\b|\bGZ\b|\bAktenzahl\b",
]
CONT_HINTS = [r"\bSeite\s*([2-9]|\d{2,})\s*(von|/)\s*\d+"]


def boundary_score(text: str) -> tuple[int, list[str]]:
    head = text[:800]
    reasons = []
    score = 0
    for pat in START_HINTS:
        if re.search(pat, head, re.I):
            score += 1
            reasons.append(pat)
    for pat in CONT_HINTS:
        if re.search(pat, text, re.I):
            score -= 3
            reasons.append("fortsetzung:" + pat)
    return score, reasons


def is_last_page(text: str) -> bool:
    m = re.search(r"\bSeite\s*(\d+)\s*(?:von|/)\s*(\d+)", text, re.I)
    return bool(m and m.group(1) == m.group(2))


def cmd_pages(args) -> None:
    texts = page_texts(Path(args.pdf))
    pages = []
    starts = []
    prev_closed = False
    for i, t in enumerate(texts, start=1):
        blank = len(t.strip()) < 15
        score, reasons = boundary_score(t)
        if prev_closed and score >= 0 and not blank:
            score += 1
            reasons.append("vorige Seite war 'Seite n von n'")
        is_start = i == 1 or (not blank and score >= 2)
        if not blank:
            prev_closed = is_last_page(t)
        if blank:
            reasons.append("leer (Trennblatt/Rueckseite?)")
        if is_start:
            starts.append(i)
        pages.append({
            "page": i,
            "blank": blank,
            "start_score": score,
            "reasons": reasons,
            "text": t[: args.chars],
        })
    groups = []
    for n, s in enumerate(starts):
        end = (starts[n + 1] - 1) if n + 1 < len(starts) else len(texts)
        groups.append([p for p in range(s, end + 1) if not pages[p - 1]["blank"]])
    out({"pdf": args.pdf, "page_count": len(texts), "suggested_documents": groups, "pages": pages})


# --------------------------------------------------------------------------- split

def cmd_split(args) -> None:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(args.pdf)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    base = Path(args.target).expanduser()
    written = []
    for doc in plan:
        folder = base / doc.get("folder", "")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe_filename(doc["name"])
        n = 2
        while target.exists():  # nie ueberschreiben
            target = folder / f"{target.stem.rsplit('_v', 1)[0]}_v{n}.pdf"
            n += 1
        w = PdfWriter()
        for p in doc["pages"]:
            w.add_page(reader.pages[int(p) - 1])
        w.add_metadata({"/Title": target.stem, "/Subject": doc.get("category", "")})
        with open(target, "wb") as fh:
            w.write(fh)
        written.append({"file": str(target), "pages": doc["pages"], "category": doc.get("category", "")})
    out({"written": written})


# --------------------------------------------------------------------------- extract

AMOUNT = r"(?:EUR|€)\s*([\d.]+,\d{2})|([\d.]+,\d{2})\s*(?:EUR|€)"
DATE = r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\b"
IBAN = r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?)\b"
GZ = r"(?:Gesch(?:ae|ä)ftszahl|GZ|Aktenzahl|Zahl|Aktenzeichen)[:\s]+([A-Z0-9][\w./\-]{3,40})"
MA = r"\b(MA\s?\d{1,2}[A-Z]?)\b"
TENANT_PATTERNS = [
    r"Mieter(?:in)?\s*[:\-]\s*(?:Herr|Frau)?\s*([A-ZÄÖÜ][\w\-]+(?:\s+[A-ZÄÖÜ][\w\-]+){1,3})",
    r"und\s+(?:Herrn?|Frau)\s+([A-ZÄÖÜ][\w\-]+(?:\s+[A-ZÄÖÜ][\w\-]+){1,3})\s*(?:,|\(|als|wohnhaft|geb)",
    r"(?:Herrn?|Frau)\s+([A-ZÄÖÜ][\w\-]+(?:\s+[A-ZÄÖÜ][\w\-]+){1,3})\s*\(?\s*(?:im Folgenden|nachstehend)?\s*[„\"]?Mieter",
]
OBJECT = r"(?:Mietgegenstand|Objekt|Wohnung|Top)\s*[:\-]?\s*([^\n]{5,80})"
RENT = r"(?:Hauptmietzins|Miete|Mietzins|Gesamtmiete)[^\d\n]{0,40}((?:EUR|€)\s*[\d.]+,\d{2}|[\d.]+,\d{2}\s*(?:EUR|€))"


def to_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def find_dates(text: str) -> list[str]:
    res = []
    for d, m, y in re.findall(DATE, text):
        try:
            res.append(dt.date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            pass
    return res


def document_date(text: str) -> str | None:
    """Briefdatum: zuerst 'Datum:' oder 'Ort, (am) TT.MM.JJJJ', sonst juengstes Datum <= heute."""
    lab = re.search(r"(?:Datum\s*:?|[A-ZÄÖÜ][a-zäöüß]+,\s*(?:am|den)?)\s*" + DATE, text)
    if lab:
        found = find_dates(lab.group(0))
        if found:
            return found[0]
    today = dt.date.today().isoformat()
    past = [d for d in find_dates(text) if d <= today]
    return max(past) if past else None


def classify(text: str) -> str:
    t = text.lower()
    if "mietvertrag" in t:
        return "mietvertrag"
    if "magistrat" in t or re.search(MA, text):
        if re.search(r"zahlung|überweis|ueberweis|auszahl|gutschrift|betrag|bescheid", t):
            return "magistrat_zahlung"
        return "magistrat_sonstiges"
    if "rechnung" in t:
        return "rechnung"
    if "kündigung" in t or "kuendigung" in t:
        return "kuendigung"
    return "sonstiges"


def load_tenants(path: str | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    raw = Path(path).read_text(encoding="utf-8-sig")
    delim = ";" if raw.splitlines()[0].count(";") >= raw.splitlines()[0].count(",") else ","
    return list(csv.DictReader(raw.splitlines(), delimiter=delim))


def match_tenant(text: str, candidates: list[str], tenants: list[dict]) -> dict | None:
    if not tenants:
        return None
    low = text.lower()
    best, best_score = None, 0.0
    for t in tenants:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        score = 0.0
        if name.lower() in low:
            score = 1.0
        else:
            parts = [p for p in name.lower().split() if len(p) > 2]
            if parts and all(p in low for p in parts):
                score = 0.95
            for c in candidates:
                score = max(score, difflib.SequenceMatcher(None, name.lower(), c.lower()).ratio())
        if score > best_score:
            best, best_score = t, score
    if best and best_score >= 0.75:
        return {**best, "match_score": round(best_score, 2)}
    return None


def extract(text: str, tenants: list[dict]) -> dict:
    amounts = []
    for a, b in re.findall(AMOUNT, text):
        try:
            amounts.append(to_float(a or b))
        except ValueError:
            pass
    tenant_candidates = []
    for pat in TENANT_PATTERNS:
        for m in re.findall(pat, text):
            name = re.sub(r"\s+", " ", m).strip()
            if name not in tenant_candidates:
                tenant_candidates.append(name)
    rent = re.search(RENT, text, re.I)
    obj = re.search(OBJECT, text)
    gz = re.search(GZ, text)
    ma = re.search(MA, text)
    category = classify(text)
    dates = find_dates(text)
    return {
        "category": category,
        "document_date": document_date(text),
        "all_dates": dates[:10],
        "amounts_eur": amounts[:10],
        "max_amount_eur": max(amounts) if amounts else None,
        "iban": [re.sub(r"\s", "", i) for i in re.findall(IBAN, text)][:3],
        "geschaeftszahl": gz.group(1) if gz else None,
        "magistratsabteilung": ma.group(1) if ma else None,
        "tenant_candidates": tenant_candidates[:5],
        "tenant_match": match_tenant(text, tenant_candidates, tenants),
        "rent_eur": to_float(re.sub(r"[^\d.,]", "", rent.group(1))) if rent else None,
        "object": obj.group(1).strip() if obj else None,
    }


def cmd_extract(args) -> None:
    pdf = Path(args.pdf)
    text = "\n".join(page_texts(pdf))
    data = extract(text, load_tenants(args.mieter))
    data["file"] = str(pdf)
    out(data)


# --------------------------------------------------------------------------- log

MIET_FIELDS = ["erfasst_am", "dokument_datum", "kategorie", "mieter", "mieter_id", "objekt",
               "betrag_eur", "miete_eur", "geschaeftszahl", "iban", "datei", "notiz"]
TASK_FIELDS = ["erfasst_am", "faellig", "aufgabe", "kategorie", "mieter", "betrag_eur", "datei", "status"]


def eur(v) -> str:
    """Betrag im oesterreichischen Format (Komma), passend zum ';'-CSV fuer Excel."""
    if v in (None, ""):
        return ""
    return f"{float(v):.2f}".replace(".", ",")


def append_csv(path: Path, fields: list[str], row: dict) -> None:
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def cmd_log(args) -> None:
    items = json.loads(Path(args.extract).read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = [items]
    logdir = Path(args.logdir).expanduser()
    logdir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    written = {"mietsoftware": 0, "assistenz": 0}
    for d in items:
        tm = d.get("tenant_match") or {}
        mieter = tm.get("name") or (d.get("tenant_candidates") or [""])[0]
        cat = d.get("category", "sonstiges")
        if cat in ("mietvertrag", "magistrat_zahlung", "kuendigung") or tm:
            append_csv(logdir / "mietsoftware_import.csv", MIET_FIELDS, {
                "erfasst_am": today,
                "dokument_datum": d.get("document_date") or "",
                "kategorie": cat,
                "mieter": mieter,
                "mieter_id": tm.get("mieter_id", ""),
                "objekt": tm.get("objekt") or d.get("object") or "",
                "betrag_eur": eur(d.get("max_amount_eur")),
                "miete_eur": eur(d.get("rent_eur")),
                "geschaeftszahl": d.get("geschaeftszahl") or "",
                "iban": ",".join(d.get("iban") or []),
                "datei": d.get("file", ""),
                "notiz": d.get("note", ""),
            })
            written["mietsoftware"] += 1
        task = d.get("task") or {
            "magistrat_zahlung": f"Magistrat-Zahlung {eur(d.get('max_amount_eur'))} EUR in Mietsoftware verbuchen ({mieter or 'Mieter pruefen'})",
            "mietvertrag": f"Mietvertrag {mieter or ''} in Mietsoftware anlegen/pruefen",
            "magistrat_sonstiges": "Schreiben vom Magistrat pruefen",
            "kuendigung": f"Kuendigung {mieter or ''} bearbeiten",
            "rechnung": f"Rechnung {eur(d.get('max_amount_eur'))} EUR pruefen/bezahlen",
        }.get(cat)
        if task:
            append_csv(logdir / "assistenz_aufgaben.csv", TASK_FIELDS, {
                "erfasst_am": today,
                "faellig": d.get("due") or "",
                "aufgabe": task.strip(),
                "kategorie": cat,
                "mieter": mieter,
                "betrag_eur": eur(d.get("max_amount_eur")),
                "datei": d.get("file", ""),
                "status": "offen",
            })
            written["assistenz"] += 1
    out({"logdir": str(logdir), "rows_written": written})


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("latest"); p.add_argument("folder"); p.set_defaults(fn=cmd_latest)
    p = sub.add_parser("ocr"); p.add_argument("input"); p.add_argument("output")
    p.add_argument("--lang", default="deu+eng"); p.set_defaults(fn=cmd_ocr)
    p = sub.add_parser("pages"); p.add_argument("pdf"); p.add_argument("--chars", type=int, default=1200)
    p.set_defaults(fn=cmd_pages)
    p = sub.add_parser("split"); p.add_argument("pdf"); p.add_argument("plan"); p.add_argument("target")
    p.set_defaults(fn=cmd_split)
    p = sub.add_parser("extract"); p.add_argument("pdf"); p.add_argument("--mieter")
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("log"); p.add_argument("extract"); p.add_argument("logdir"); p.set_defaults(fn=cmd_log)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
