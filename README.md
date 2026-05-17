# Versuchsprotokoll_WK

Interaktives Versuchsprotokoll als GSM-Klassenraum-App:

- Pseudonym-Schülerlogin mit Datenschutz-/KI-Hinweis
- ein Arbeitsblatt-Bereich ohne zusätzliche Reiter
- einfaches Protokollformular ohne Niveaustufen
- Lehrerdashboard mit Live-Fortschritt, Detailansicht und Antwortprotokoll
- PDF-Export des fertigen Protokolls pro Schüler im Lehrerbereich
- KI-Tutor erst nach Lehrerfreigabe
- tägliches Token-Limit über `DAILY_TOKEN_LIMIT`
- SQLite/WAL für lokale Sitzungsdaten

## Lokal starten

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Wichtige Umgebungsvariablen:

```bash
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.1-flash-lite
SECRET_KEY=...
LEHRER_PASSWORD=...
DAILY_TOKEN_LIMIT=50000
```

Schülerbereich: `http://127.0.0.1:5000/`

Lehrerbereich: `http://127.0.0.1:5000/lehrer`

## Speicherung und Export

Die laufenden Daten werden in SQLite gespeichert (`data/versuchsprotokoll.sqlite3`), nicht als JSON. Der PDF-Export wird serverseitig erzeugt und funktioniert dadurch auch auf iPad/iOS als normaler Download aus dem Lehrerbereich.

## Deployment

Siehe [DEPLOYMENT.md](DEPLOYMENT.md).
