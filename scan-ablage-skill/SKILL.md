---
name: scan-ablage-skill
description: Neuesten Scan aus dem Dropbox-Ordner "_SCAN" holen, OCR (Deutsch+Englisch), in Einzeldokumente zerlegen, als durchsuchbare PDFs mit sprechendem Namen ablegen und Inhalte auswerten (Magistrat-Zahlungen, Mietverträge, Mietername) für Mietsoftware und Assistenz-Aufgabenliste. Verwenden bei "Scan ablegen", "letzten Scan verarbeiten", "Post einscannen und sortieren".
---

# Scan-Ablage Skill

Verarbeitet den letzten Scan aus Dropbox komplett: OCR, Zerlegen, Ablage, Auswertung.
Das Hilfsskript `scripts/scan_ablage.py` macht die mechanische Arbeit; die inhaltlichen
Entscheidungen (wo endet ein Dokument, wie heißt es, wem gehört es) triffst du.

## Voraussetzungen

- `python3` mit `pypdf`. Auf dem Mac hat Homebrew-`python3` (3.15) kein `pypdf`, `/usr/bin/python3` schon.
  Interpreter einmal bestimmen und überall verwenden:
  `PY=$(for p in python3 /usr/bin/python3 python3.12 python3.13; do $p -c 'import pypdf' 2>/dev/null && echo $p && break; done)`.
  Fehlt `pypdf` überall: `$PY -m pip install --user pypdf`. Das Skript meldet einen fehlenden `pypdf` als JSON-Fehler.
- `ocrmypdf` und `tesseract` mit Sprachpaketen `deu` und `eng`
  (`pip install ocrmypdf`, `apt install tesseract-ocr tesseract-ocr-deu` bzw. `brew install ocrmypdf tesseract-lang`)
- **Mietsoftware** (Patricks eigene App auf dem Server 0.8). Ihr MCP `vermietung-mcp` (Port 8767)
  bietet nur `submit_bank_eingang` für Bankeingänge. Mieter- und Vertragsdaten ändert man nur über
  die Web-Routen der App (z. B. `POST /mieter/{id}/bearbeiten`), siehe Schritt 7.
- `mieter.csv` (Spalten `name;mieter_id;objekt`) für die Zuordnung zu Mietern.

## Standardpfade (falls der Nutzer nichts anderes sagt)

| Zweck | Pfad |
|---|---|
| Dropbox-Wurzel | macOS: `~/Library/CloudStorage/Dropbox/`, sonst `~/Dropbox/` (die vorhandene nehmen; unten kurz `~/Dropbox/`) |
| Eingang | `~/Dropbox/_SCAN/` |
| Ablage | `~/Dropbox/Ablage/` |
| Mieterliste | `~/Dropbox/Ablage/mieter.csv` |
| Logs | `~/Dropbox/Ablage/_Logs/` (`mietsoftware_import.csv`, `assistenz_aufgaben.csv`) |
| Arbeitsordner | temporärer Ordner, nicht in Dropbox |

Ohne lokalen Dropbox-Ordner: Datei mit dem Dropbox-Connector finden (`list_folder` auf `/_SCAN`,
nach `server_modified` sortieren), per `download_link` herunterladen, verarbeiten und dem Nutzer
die fertigen PDFs übergeben. Der Connector kann keine PDFs hochladen, darum ist der lokal
synchronisierte Dropbox-Ordner der bevorzugte Weg.

## Ablauf

`S=<skill-ordner>/scripts/scan_ablage.py`, `$PY` wie unter Voraussetzungen.

0. **Mietsoftware prüfen**
   Per ToolSearch nach `vermietung` bzw. `submit_bank_eingang` suchen und das Schema lesen, nicht raten.
   Fehlt der MCP, läuft alles über die CSV-Dateien (Schritt 7).

1. **Neuesten Scan finden**
   `$PY $S latest ~/Dropbox/_SCAN` und dem Nutzer Dateiname und Datum nennen.
   Der Scanner legt fertige PDFs teils als `*.pdf.part` ab. `latest` nimmt sie mit, wenn sie
   vollständig sind (`%%EOF` vorhanden und `pdfinfo` ok), und überspringt `_verarbeitet/`.

2. **OCR**
   `$PY $S ocr <scan> <arbeitsordner>/ocr.pdf`
   Standard ist `--force-ocr` mit `--rotate-pages` und `--deskew`: Der Textlayer des Scanners
   (ExactScan) ist oft schlecht und bei verkehrt herum gescannten Seiten unbrauchbar, darum wird
   neu erkannt. `--keep-text` behält einen vorhandenen guten Textlayer (schneller).

3. **Dokumentgrenzen bestimmen**
   `$PY $S pages <arbeitsordner>/ocr.pdf`
   liefert den Text jeder Seite und `suggested_documents`. Den Vorschlag **selbst prüfen**:
   neuer Briefkopf, neuer Absender, neues Datum, "Seite 1 von n" beginnt ein Dokument;
   "Seite 2 von n" und Anlagen gehören zum vorherigen. Leere Seiten (Rückseiten) weglassen.
   **Verkehrte Stapel:** Stapel kommen oft umgekehrt aus dem Scanner, mehrseitige Dokumente laufen
   dann von der letzten zur ersten Seite. `reverse_order_suspected: true` weist darauf hin (absteigende
   "Seite n von m"). Der Grenzvorschlag gilt nur für Vorwärtsreihenfolge, die Grenzen dann selbst
   bestimmen und im Plan die Seiten in Lesereihenfolge angeben, z. B. `"pages": [7, 6, 5]`.

4. **Plan schreiben** (`plan.json`), ein Eintrag pro Dokument:
   ```json
   [{"pages": [1], "folder": "Mieter/Huber_Anna/Magistrat",
     "name": "2026-11-03_Magistrat-Wien-MA40_Mietbeihilfe-Huber.pdf",
     "category": "magistrat_zahlung"}]
   ```
   - **Dateiname:** `JJJJ-MM-TT_Absender_Thema.pdf` (Briefdatum, nicht Scandatum; keine Umlaute
     und Leerzeichen, das Skript bereinigt sie zusätzlich).
   - **Ordner:**
     - Mieterbezogen: `Mieter/<Nachname>_<Vorname>/{Mietvertrag,Magistrat,Korrespondenz}`
     - Rechnungen: `Rechnungen/<JJJJ>`
     - Versicherungen: `Versicherungen/<Gesellschaft>`
     - Behörden ohne Mieterbezug: `Behoerden/<Stelle>`
     - Unklar: `Unsortiert`
   - **category:** `magistrat_zahlung`, `magistrat_sonstiges`, `mietvertrag`, `kuendigung`, `rechnung`,
     `versicherung`, `sonstiges`. Statutarstädte schreiben unter eigenem Namen ("Stadt Wels / Steuerverwaltung",
     Abgabenvorschreibung): das zählt als Magistrat.

5. **Zerlegen und ablegen**
   `$PY $S split <arbeitsordner>/ocr.pdf plan.json ~/Dropbox/Ablage`
   Jede Datei bleibt durchsuchbar. Vorhandene Dateien werden nie überschrieben (`_v2` usw.).

6. **Inhalt auswerten**, für jede geschriebene Datei:
   `$PY $S extract <datei> --mieter ~/Dropbox/Ablage/mieter.csv`
   Das liefert Kategorie, Briefdatum, Beträge, IBAN, Geschäftszahl, Magistratsabteilung (MA xx),
   Mieter-Kandidaten, Treffer aus der Mieterliste, Mietzins und Objekt.
   Ergebnisse **gegen den OCR-Text prüfen** und korrigieren. Mieternamen sind in Mietverträgen oft
   **handschriftlich** eingetragen; OCR erkennt sie nicht. Dann die Seite als Bild ansehen
   (z. B. `pdftoppm -r 150 -png -f N -l N ocr.pdf seite`, dann das PNG lesen) und den Namen visuell
   ablesen; unsichere Lesungen als unsicher melden. Bei Mietverträgen:
   Mieter, Objekt/Top, Mietbeginn, Hauptmietzins, Kaution. Bei Magistrat-Zahlungen:
   Betrag, Geschäftszahl, Zeitraum, begünstigter Mieter.
   Optional pro Eintrag `"note"`, `"due"` (Fälligkeit JJJJ-MM-TT) und `"task"` (eigener Aufgabentext) ergänzen.
   Alle Einträge als JSON-Liste in `extract.json` speichern.

7. **Mietsoftware und Assistenz-Liste füllen**
   Dem Nutzer zuerst eine Tabelle der geplanten Einträge zeigen (Mieter, Art, Betrag, Datum, Datei)
   und erst nach Bestätigung schreiben.
   - **Bankeingänge** (z. B. eine Magistrat-Zahlung, die als Mietbeihilfe an den Vermieter überwiesen wurde):
     über `vermietung-mcp` → `submit_bank_eingang` eintragen, Felder laut Schema
     (Betrag, Datum, Mieter, Geschäftszahl und PDF-Pfad als Verwendungszweck/Referenz). Rückgabe-ID als `"note"` übernehmen.
   - **Mieter- und Vertragsdaten** (neuer Mietvertrag, Mietzins, Objekt/Top, Kaution, neuer Mieter):
     Der MCP kann das nicht. Nur auf ausdrücklichen Wunsch über die Web-Routen der App auf dem Server 0.8
     (z. B. `POST /mieter/{id}/bearbeiten`; Formularfelder vorher aus der Seite `GET /mieter/{id}/bearbeiten` lesen).
     Sonst als Aufgabe in `assistenz_aufgaben.csv` lassen.
   - **Immer** danach `$PY $S log extract.json ~/Dropbox/Ablage/_Logs` ausführen: protokolliert alles
     und ist der Weg für alles, was nicht direkt eingetragen wurde.
   - `mietsoftware_import.csv`: Mietverträge, Magistrat-Zahlungen, Kündigungen und alles mit Mieterbezug
     (`;`-getrennt, Beträge mit Komma, direkt in Excel/Mietsoftware importierbar).
   - `assistenz_aufgaben.csv`: eine offene Aufgabe pro Dokument (z. B. "Magistrat-Zahlung 245,50 EUR verbuchen").

8. **Neuer Mieter?** Wird in einem Mietvertrag ein Mieter gefunden, der nicht in `mieter.csv` steht,
   den Nutzer fragen und nach Bestätigung eine Zeile anhängen.

9. **Original:** Den Scan nach `~/Dropbox/_SCAN/_verarbeitet/` verschieben (eine `.pdf.part` dabei in `.pdf` umbenennen), nicht löschen.
   Löschen nur auf ausdrücklichen Wunsch.

## Abschlussbericht an den Nutzer

Kurze Tabelle: Datei (Pfad) | Kategorie | Mieter | Betrag | Aufgabe. Danach unsichere Punkte nennen
(schlechte OCR, Mieter nicht gefunden, Grenze zwischen Dokumenten unklar).

## Regeln

- Keine Daten erfinden: Was nicht im Text steht, bleibt leer und wird als offen gemeldet.
- Mieterdaten, IBANs und Beträge nicht an externe Dienste senden.
- Bei Mieter-Treffern mit `match_score` unter 0.9 nachfragen, bevor verbucht wird.
