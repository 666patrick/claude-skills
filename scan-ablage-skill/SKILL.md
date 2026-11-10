---
name: scan-ablage-skill
description: Neuesten Scan aus dem Dropbox-Ordner "Scan" holen, OCR (Deutsch+Englisch), in Einzeldokumente zerlegen, als durchsuchbare PDFs mit sprechendem Namen ablegen und Inhalte auswerten (Magistrat-Zahlungen, Mietverträge, Mietername) für Mietsoftware und Assistenz-Aufgabenliste. Verwenden bei "Scan ablegen", "letzten Scan verarbeiten", "Post einscannen und sortieren".
---

# Scan-Ablage Skill

Verarbeitet den letzten Scan aus Dropbox komplett: OCR, Zerlegen, Ablage, Auswertung.
Das Hilfsskript `scripts/scan_ablage.py` macht die mechanische Arbeit; die inhaltlichen
Entscheidungen (wo endet ein Dokument, wie heißt es, wem gehört es) triffst du.

## Voraussetzungen

- `python3` mit `pypdf`
- `ocrmypdf` und `tesseract` mit Sprachpaketen `deu` und `eng`
  (`pip install ocrmypdf`, `apt install tesseract-ocr tesseract-ocr-deu` bzw. `brew install ocrmypdf tesseract-lang`)
- **Mietsoftware-MCP** (Patricks eigene Mietsoftware, läuft als Claude-Code-Session/MCP auf dem Server 0.8).
  Ist er verbunden, ist er die führende Quelle für Mieter und das Ziel für Buchungen.
  Ohne ihn: `mieter.csv` (Spalten `name;mieter_id;objekt`) und CSV-Import als Fallback.

## Standardpfade (falls der Nutzer nichts anderes sagt)

| Zweck | Pfad |
|---|---|
| Dropbox-Wurzel | macOS: `~/Library/CloudStorage/Dropbox/`, sonst `~/Dropbox/` (die vorhandene nehmen; unten kurz `~/Dropbox/`) |
| Eingang | `~/Dropbox/Scan/` |
| Ablage | `~/Dropbox/Ablage/` |
| Mieterliste | `~/Dropbox/Ablage/mieter.csv` |
| Logs | `~/Dropbox/Ablage/_Logs/` (`mietsoftware_import.csv`, `assistenz_aufgaben.csv`) |
| Arbeitsordner | temporärer Ordner, nicht in Dropbox |

Ohne lokalen Dropbox-Ordner: Datei mit dem Dropbox-Connector finden (`list_folder` auf `/Scan`,
nach `server_modified` sortieren), per `download_link` herunterladen, verarbeiten und dem Nutzer
die fertigen PDFs übergeben. Der Connector kann keine PDFs hochladen, darum ist der lokal
synchronisierte Dropbox-Ordner der bevorzugte Weg.

## Ablauf

`S=<skill-ordner>/scripts/scan_ablage.py`

0. **Mietsoftware-MCP suchen**
   Per ToolSearch nach `miet`, `mieter`, `tenant`, `rent` suchen. Gefundene Tools merken
   (typisch: Mieter auflisten/suchen, Mietvertrag anlegen, Zahlung buchen) und ihre Schemas lesen,
   nicht raten. Gibt es eine Mieterliste-Funktion, deren Ergebnis als `mieter.csv`
   (`name;mieter_id;objekt`) in den Arbeitsordner schreiben, damit Schritt 6 dagegen abgleicht.

1. **Neuesten Scan finden**
   `python3 $S latest ~/Dropbox/Scan` und dem Nutzer Dateiname und Datum nennen.

2. **OCR**
   `python3 $S ocr <scan> <arbeitsordner>/ocr.pdf`
   (deu+eng, dreht und begradigt Seiten, überspringt bereits durchsuchbare PDFs).

3. **Dokumentgrenzen bestimmen**
   `python3 $S pages <arbeitsordner>/ocr.pdf`
   liefert den Text jeder Seite und `suggested_documents`. Den Vorschlag **selbst prüfen**:
   neuer Briefkopf, neuer Absender, neues Datum, "Seite 1 von n" beginnt ein Dokument;
   "Seite 2 von n" und Anlagen gehören zum vorherigen. Leere Seiten (Rückseiten) weglassen.

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
     - Behörden ohne Mieterbezug: `Behoerden/<Stelle>`
     - Unklar: `Unsortiert`
   - **category:** `magistrat_zahlung`, `magistrat_sonstiges`, `mietvertrag`, `kuendigung`, `rechnung`, `sonstiges`.

5. **Zerlegen und ablegen**
   `python3 $S split <arbeitsordner>/ocr.pdf plan.json ~/Dropbox/Ablage`
   Jede Datei bleibt durchsuchbar. Vorhandene Dateien werden nie überschrieben (`_v2` usw.).

6. **Inhalt auswerten**, für jede geschriebene Datei:
   `python3 $S extract <datei> --mieter ~/Dropbox/Ablage/mieter.csv`
   Das liefert Kategorie, Briefdatum, Beträge, IBAN, Geschäftszahl, Magistratsabteilung (MA xx),
   Mieter-Kandidaten, Treffer aus der Mieterliste, Mietzins und Objekt.
   Ergebnisse **gegen den OCR-Text prüfen** und korrigieren. Bei Mietverträgen:
   Mieter, Objekt/Top, Mietbeginn, Hauptmietzins, Kaution. Bei Magistrat-Zahlungen:
   Betrag, Geschäftszahl, Zeitraum, begünstigter Mieter.
   Optional pro Eintrag `"note"`, `"due"` (Fälligkeit JJJJ-MM-TT) und `"task"` (eigener Aufgabentext) ergänzen.
   Alle Einträge als JSON-Liste in `extract.json` speichern.

7. **Mietsoftware und Assistenz-Liste füllen**
   - **Mit Mietsoftware-MCP:** dem Nutzer zuerst eine Tabelle der geplanten Buchungen zeigen
     (Mieter, Art, Betrag, Datum, Datei) und nach Bestätigung über die MCP-Tools eintragen:
     Magistrat-Zahlung beim zugeordneten Mieter verbuchen (Geschäftszahl und PDF-Pfad als Referenz),
     Mietvertrag beim Mieter anlegen bzw. ergänzen (Objekt/Top, Beginn, Hauptmietzins, Kaution, PDF-Pfad).
     Die IDs, die der MCP zurückgibt, als `"note"` in `extract.json` übernehmen.
   - **Immer** danach `python3 $S log extract.json ~/Dropbox/Ablage/_Logs` ausführen: protokolliert alles
     und ist ohne MCP der Import-Weg.
   - `mietsoftware_import.csv`: Mietverträge, Magistrat-Zahlungen, Kündigungen und alles mit Mieterbezug
     (`;`-getrennt, Beträge mit Komma, direkt in Excel/Mietsoftware importierbar).
   - `assistenz_aufgaben.csv`: eine offene Aufgabe pro Dokument (z. B. "Magistrat-Zahlung 245,50 EUR verbuchen").

8. **Neuer Mieter?** Wird in einem Mietvertrag ein Mieter gefunden, der nicht in `mieter.csv` steht,
   den Nutzer fragen und nach Bestätigung eine Zeile anhängen.

9. **Original:** Den Scan nach `~/Dropbox/Scan/_verarbeitet/` verschieben, nicht löschen.
   Löschen nur auf ausdrücklichen Wunsch.

## Abschlussbericht an den Nutzer

Kurze Tabelle: Datei (Pfad) | Kategorie | Mieter | Betrag | Aufgabe. Danach unsichere Punkte nennen
(schlechte OCR, Mieter nicht gefunden, Grenze zwischen Dokumenten unklar).

## Regeln

- Keine Daten erfinden: Was nicht im Text steht, bleibt leer und wird als offen gemeldet.
- Mieterdaten, IBANs und Beträge nicht an externe Dienste senden.
- Bei Mieter-Treffern mit `match_score` unter 0.9 nachfragen, bevor verbucht wird.
