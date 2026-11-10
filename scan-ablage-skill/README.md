# Scan-Ablage Skill

Holt den neuesten Scan aus `Dropbox/Scan`, macht OCR (Deutsch+Englisch), zerlegt ihn in die
einzelnen Dokumente, legt jedes als durchsuchbares PDF mit sprechendem Namen ab und wertet den
Inhalt aus: Magistrat-Zahlungen, Mietverträge und Mietername. Ergebnis landet in einer
Import-CSV für die Mietsoftware und in einer Aufgabenliste für die persönliche Assistenz.

## Features

- Neuesten Scan finden (`latest`)
- OCR mit `ocrmypdf` / `tesseract` (`ocr`)
- Text pro Seite und Vorschlag für Dokumentgrenzen (`pages`)
- Zerlegen nach Plan in Zielordner, ohne zu überschreiben (`split`)
- Kerndaten auslesen und Mieter aus `mieter.csv` zuordnen (`extract`)
- `mietsoftware_import.csv` und `assistenz_aufgaben.csv` fortschreiben (`log`)

## Installation

```bash
cp -r scan-ablage-skill ~/.claude/skills/
pip install pypdf ocrmypdf
sudo apt install tesseract-ocr tesseract-ocr-deu   # macOS: brew install ocrmypdf tesseract-lang
cp scan-ablage-skill/mieter.example.csv ~/Dropbox/Ablage/mieter.csv   # und anpassen
```

## Beispiel

In Claude Code: *"Verarbeite den letzten Scan."*

Manuell:

```bash
S=~/.claude/skills/scan-ablage-skill/scripts/scan_ablage.py
python3 $S latest ~/Dropbox/Scan
python3 $S ocr ~/Dropbox/Scan/Scan_2026-11-10.pdf /tmp/scan/ocr.pdf
python3 $S pages /tmp/scan/ocr.pdf          # -> suggested_documents: [[1], [2, 3], [4]]
python3 $S split /tmp/scan/ocr.pdf plan.json ~/Dropbox/Ablage
python3 $S extract ~/Dropbox/Ablage/Mieter/Huber_Anna/Magistrat/2026-11-03_Magistrat-Wien-MA40_Mietbeihilfe-Huber.pdf \
  --mieter ~/Dropbox/Ablage/mieter.csv
python3 $S log extract.json ~/Dropbox/Ablage/_Logs
```

Ergebnis für einen 4-seitigen Test-Scan:

```
Ablage/
├── Mieter/Huber_Anna/Magistrat/2026-11-03_Magistrat-Wien-MA40_Mietbeihilfe-Huber.pdf
├── Mieter/Mustermann_Max/Mietvertrag/2026-11-05_Mustermann-Max_Mietvertrag-Hauptstrasse-12-Top-3.pdf
├── Rechnungen/2026/2026-11-06_Installateur-Meier_Rechnung-Therme-Top-3.pdf
└── _Logs/
    ├── mietsoftware_import.csv
    └── assistenz_aufgaben.csv
```

`mietsoftware_import.csv`:

```
erfasst_am;dokument_datum;kategorie;mieter;mieter_id;objekt;betrag_eur;miete_eur;geschaeftszahl;iban;datei;notiz
2026-11-10;2026-11-03;magistrat_zahlung;Anna Huber;M-007;Hauptstrasse 12 Top 7;245,50;;MA40-2026/123456;AT61...;...;
2026-11-10;2026-11-05;mietvertrag;Max Mustermann;M-003;Hauptstrasse 12 Top 3;650,00;650,00;;;...;
```

`assistenz_aufgaben.csv`:

```
erfasst_am;faellig;aufgabe;kategorie;mieter;betrag_eur;datei;status
2026-11-10;;Magistrat-Zahlung 245,50 EUR in Mietsoftware verbuchen (Anna Huber);magistrat_zahlung;Anna Huber;245,50;...;offen
```

## Configuration

Ablauf und Ordnerkonvention stehen in `SKILL.md`. Mieterliste: `mieter.example.csv`.
