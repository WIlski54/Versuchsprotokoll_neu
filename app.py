import base64
import datetime as dt
import html
import json
import os
import re
import secrets
import sqlite3
import uuid
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_socketio import SocketIO, emit, join_room
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "versuchsprotokoll.sqlite3"

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
socketio = SocketIO(app, async_mode="threading")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
DAILY_TOKEN_LIMIT = int(os.getenv("DAILY_TOKEN_LIMIT", "50000"))
LEHRER_PASSWORD = os.getenv("LEHRER_PASSWORD", "wechseln")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
PDF_FONT = "Helvetica"
PDF_FONT_BOLD = "Helvetica-Bold"

TASKS = [
    {"id": "date", "title": "Datum", "short": "Datum"},
    {"id": "question", "title": "Versuchsfrage", "short": "Frage"},
    {"id": "material", "title": "Material", "short": "Material"},
    {"id": "safety", "title": "Sicherheitsmaßnahmen", "short": "Sicherheit"},
    {"id": "setup", "title": "Versuchsaufbau", "short": "Aufbau"},
    {"id": "instructions", "title": "Versuchsanleitung", "short": "Anleitung"},
    {"id": "observation", "title": "Beobachtungen", "short": "Beobachtung"},
    {"id": "result", "title": "Ergebnis und Auswertung", "short": "Auswertung"},
]

PROTOCOL_HINTS = {
    "date": "Trage das Datum des Versuchstags ein.",
    "question": "Formuliere eine klare Frage, die sich mit dem Versuch beantworten lässt.",
    "material": "Liste Geräte, Stoffe und wichtige Mengen übersichtlich auf.",
    "safety": "Notiere Schutzmaßnahmen, Gefahren und Entsorgungshinweise.",
    "setup": "Beschreibe den Aufbau so, dass eine andere Gruppe ihn nachvollziehen kann. Du kannst zusätzlich ein Foto hochladen.",
    "instructions": "Schreibe die Durchführung in geordneten Arbeitsschritten.",
    "observation": "Beschreibe nur, was du beobachtet oder gemessen hast. Trenne Beobachtung und Erklärung.",
    "result": "Beantworte die Versuchsfrage und deute deine Beobachtungen fachlich.",
}


def configure_pdf_fonts():
    global PDF_FONT, PDF_FONT_BOLD
    candidates = [
        ("Arial", "Arial-Bold", Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        ("DejaVuSans", "DejaVuSans-Bold", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ]
    for regular_name, bold_name, regular_path, bold_path in candidates:
        if regular_path.exists() and bold_path.exists():
            pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
            pdfmetrics.registerFontFamily(regular_name, normal=regular_name, bold=bold_name)
            PDF_FONT = regular_name
            PDF_FONT_BOLD = bold_name
            return


configure_pdf_fonts()


@app.context_processor
def template_helpers():
    return {"protocol_hint": lambda task_id: PROTOCOL_HINTS.get(task_id, "")}


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def today_key():
    return dt.date.today().isoformat()


def get_db():
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schueler (
                id TEXT PRIMARY KEY,
                pseudonym TEXT NOT NULL,
                kurs TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_active TEXT NOT NULL,
                socket_id TEXT
            );

            CREATE TABLE IF NOT EXISTS antworten (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schueler_id TEXT NOT NULL,
                aufgabe TEXT NOT NULL,
                niveau TEXT NOT NULL DEFAULT 'standard',
                inhalt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (schueler_id) REFERENCES schueler(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS fortschritt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schueler_id TEXT NOT NULL,
                aufgabe TEXT NOT NULL,
                niveau TEXT NOT NULL DEFAULT 'standard',
                completed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (schueler_id) REFERENCES schueler(id) ON DELETE CASCADE,
                UNIQUE (schueler_id, aufgabe)
            );

            CREATE TABLE IF NOT EXISTS ki_anfragen (
                id TEXT PRIMARY KEY,
                schueler_id TEXT NOT NULL,
                frage TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                decided_at TEXT,
                FOREIGN KEY (schueler_id) REFERENCES schueler(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schueler_id TEXT NOT NULL,
                rolle TEXT NOT NULL,
                inhalt TEXT NOT NULL,
                tokens INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (schueler_id) REFERENCES schueler(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS token_usage (
                datum TEXT PRIMARY KEY,
                tokens INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS setup_images (
                schueler_id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (schueler_id) REFERENCES schueler(id) ON DELETE CASCADE
            );
            """
        )
        db.commit()


@app.before_request
def ensure_db():
    init_db()


def teacher_required():
    return bool(session.get("is_lehrer"))


def student_required():
    return session.get("schueler_id")


def require_teacher_action():
    token = request.headers.get("X-Lehrer-Token", "")
    return teacher_required() and token and token == session.get("lehrer_action_token")


def row_to_dict(row):
    return dict(row) if row else None


def student_snapshot():
    with get_db() as db:
        students = [dict(row) for row in db.execute(
            """
            SELECT s.*,
                   COALESCE(done.done_count, 0) AS done_count,
                   COALESCE(pending.pending_count, 0) AS pending_count
            FROM schueler s
            LEFT JOIN (
                SELECT schueler_id, COUNT(*) AS done_count
                FROM fortschritt
                WHERE completed=1
                GROUP BY schueler_id
            ) done ON done.schueler_id=s.id
            LEFT JOIN (
                SELECT schueler_id, COUNT(*) AS pending_count
                FROM ki_anfragen
                WHERE status='pending'
                GROUP BY schueler_id
            ) pending ON pending.schueler_id=s.id
            ORDER BY s.created_at DESC
            """
        )]
        requests = [dict(row) for row in db.execute(
            """
            SELECT k.*, s.pseudonym, s.kurs
            FROM ki_anfragen k
            JOIN schueler s ON s.id=k.schueler_id
            ORDER BY k.created_at DESC
            LIMIT 50
            """
        )]
        usage = get_token_usage(db)
    return {"students": students, "requests": requests, "usage": usage, "task_count": len(TASKS)}


def detail_snapshot(sid):
    with get_db() as db:
        student = row_to_dict(db.execute("SELECT * FROM schueler WHERE id=?", (sid,)).fetchone())
        answers = [dict(row) for row in db.execute(
            "SELECT * FROM antworten WHERE schueler_id=? ORDER BY created_at DESC LIMIT 200",
            (sid,),
        )]
        chats = [dict(row) for row in db.execute(
            "SELECT * FROM chat_messages WHERE schueler_id=? ORDER BY created_at ASC",
            (sid,),
        )]
        requests = [dict(row) for row in db.execute(
            "SELECT * FROM ki_anfragen WHERE schueler_id=? ORDER BY created_at DESC",
            (sid,),
        )]
        image_row = row_to_dict(db.execute("SELECT * FROM setup_images WHERE schueler_id=?", (sid,)).fetchone())
    return {"student": student, "answers": answers, "chats": chats, "requests": requests, "tasks": TASKS, "setup_image": image_row}


def get_token_usage(db=None):
    owns_db = db is None
    db = db or get_db()
    try:
        row = db.execute("SELECT tokens FROM token_usage WHERE datum=?", (today_key(),)).fetchone()
        used = int(row["tokens"]) if row else 0
        return {"today": used, "limit": DAILY_TOKEN_LIMIT, "remaining": max(DAILY_TOKEN_LIMIT - used, 0)}
    finally:
        if owns_db:
            db.close()


def add_tokens(amount):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO token_usage (datum, tokens) VALUES (?, ?)
            ON CONFLICT(datum) DO UPDATE SET tokens = tokens + excluded.tokens
            """,
            (today_key(), amount),
        )
        db.commit()
        usage = get_token_usage(db)
    socketio.emit("token_update", usage, to="lehrer_room")
    return usage


def estimate_tokens(*parts):
    text = "\n".join(str(part or "") for part in parts)
    return max(1, len(text) // 4)


def latest_approved_request(sid):
    with get_db() as db:
        return row_to_dict(db.execute(
            """
            SELECT * FROM ki_anfragen
            WHERE schueler_id=? AND status='approved'
            ORDER BY decided_at DESC, created_at DESC
            LIMIT 1
            """,
            (sid,),
        ).fetchone())


def format_protocol(sid):
    with get_db() as db:
        latest = {}
        for row in db.execute(
            "SELECT aufgabe, inhalt FROM antworten WHERE schueler_id=? ORDER BY created_at ASC",
            (sid,),
        ):
            latest[row["aufgabe"]] = row["inhalt"]
    labels = {task["id"]: task["title"] for task in TASKS}
    return "\n".join(f"{labels[key]}: {latest.get(key, '-')}" for key in labels)


@app.get("/")
def index():
    if not student_required():
        return redirect(url_for("login"))
    return render_template("index.html", tasks=TASKS, schueler=session)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pseudonym = request.form.get("pseudonym", "").strip()[:40]
        kurs = request.form.get("kurs", "").strip()[:40]
        consent = request.form.get("consent") == "on"
        if not pseudonym or not kurs or not consent:
            return render_template("login.html", error="Bitte Pseudonym, Kurs und den Hinweis bestätigen.")
        sid = uuid.uuid4().hex
        with get_db() as db:
            db.execute(
                "INSERT INTO schueler (id, pseudonym, kurs, created_at, last_active) VALUES (?, ?, ?, ?, ?)",
                (sid, pseudonym, kurs, now_iso(), now_iso()),
            )
            db.commit()
        session.clear()
        session["schueler_id"] = sid
        session["pseudonym"] = pseudonym
        session["kurs"] = kurs
        socketio.emit("neuer_schueler", {"id": sid, "pseudonym": pseudonym, "kurs": kurs}, to="lehrer_room")
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/lehrer", methods=["GET", "POST"])
def lehrer_login():
    if request.method == "POST":
        if request.form.get("password") != LEHRER_PASSWORD:
            return render_template("lehrer_login.html", error="Passwort stimmt nicht.")
        session["is_lehrer"] = True
        session["lehrer_action_token"] = secrets.token_urlsafe(24)
        return redirect(url_for("dashboard"))
    return render_template("lehrer_login.html")


@app.post("/lehrer/logout")
def lehrer_logout():
    session.pop("is_lehrer", None)
    session.pop("lehrer_action_token", None)
    return redirect(url_for("lehrer_login"))


@app.get("/dashboard")
def dashboard():
    if not teacher_required():
        return redirect(url_for("lehrer_login"))
    return render_template("dashboard.html", snapshot=student_snapshot(), action_token=session["lehrer_action_token"])


@app.get("/schueler/<sid>")
def schueler_detail(sid):
    if not teacher_required():
        return redirect(url_for("lehrer_login"))
    snap = detail_snapshot(sid)
    if not snap["student"]:
        return redirect(url_for("dashboard"))
    return render_template("schueler_detail.html", snapshot=snap, action_token=session["lehrer_action_token"])


@app.get("/schueler/<sid>/export.pdf")
def export_student_pdf(sid):
    if not teacher_required():
        return redirect(url_for("lehrer_login"))
    snap = detail_snapshot(sid)
    if not snap["student"]:
        return redirect(url_for("dashboard"))
    pdf = build_protocol_pdf(snap)
    filename = safe_filename(f"Versuchsprotokoll_{snap['student']['pseudonym']}.pdf")
    return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "model": GEMINI_MODEL, "daily_token_limit": DAILY_TOKEN_LIMIT})


@app.post("/api/save-answer")
def save_answer():
    sid = student_required()
    if not sid:
        return jsonify({"error": "Nicht angemeldet."}), 401
    data = request.get_json(silent=True) or {}
    aufgabe = str(data.get("task", "")).strip()
    inhalt = str(data.get("content", "")).strip()
    if aufgabe not in {task["id"] for task in TASKS}:
        return jsonify({"error": "Ungültige Aufgabe."}), 400
    if not inhalt:
        return jsonify({"error": "Bitte trage zuerst etwas ein."}), 400
    timestamp = now_iso()
    with get_db() as db:
        db.execute(
            "INSERT INTO antworten (schueler_id, aufgabe, niveau, inhalt, created_at) VALUES (?, ?, 'standard', ?, ?)",
            (sid, aufgabe, inhalt, timestamp),
        )
        db.execute(
            """
            INSERT INTO fortschritt (schueler_id, aufgabe, niveau, completed, updated_at)
            VALUES (?, ?, 'standard', 1, ?)
            ON CONFLICT(schueler_id, aufgabe) DO UPDATE SET
                niveau='standard',
                completed=1,
                updated_at=excluded.updated_at
            """,
            (sid, aufgabe, timestamp),
        )
        db.commit()
    if aufgabe == "setup" and data.get("image"):
        save_setup_image(sid, str(data.get("image")), str(data.get("image_name", "versuchsaufbau.png")))
    answer_event = {"id": sid, "aufgabe": aufgabe, "inhalt": inhalt, "created_at": timestamp}
    socketio.emit("fortschritt_update", answer_event, to="lehrer_room")
    socketio.emit("antwort_live", answer_event, to=f"watch_{sid}")
    return jsonify({"ok": True})


@app.post("/api/ki/request")
def request_ki():
    sid = student_required()
    if not sid:
        return jsonify({"error": "Nicht angemeldet."}), 401
    data = request.get_json(silent=True) or {}
    frage = str(data.get("question", "")).strip()
    if not frage:
        return jsonify({"error": "Bitte stelle zuerst deine Frage im Chat."}), 400
    request_id = uuid.uuid4().hex
    timestamp = now_iso()
    with get_db() as db:
        db.execute(
            "INSERT INTO ki_anfragen (id, schueler_id, frage, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (request_id, sid, frage, timestamp),
        )
        db.commit()
        student = row_to_dict(db.execute("SELECT pseudonym, kurs FROM schueler WHERE id=?", (sid,)).fetchone())
    event = {
        "id": request_id,
        "schueler_id": sid,
        "frage": frage,
        "status": "pending",
        "created_at": timestamp,
        "pseudonym": student["pseudonym"],
        "kurs": student["kurs"],
    }
    socketio.emit("neue_anfrage", event, to="lehrer_room")
    return jsonify({"ok": True, "request_id": request_id, "status": "pending"})


@app.post("/api/ki/chat")
def ki_chat():
    sid = student_required()
    if not sid:
        return jsonify({"error": "Nicht angemeldet."}), 401
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Bitte schreibe eine Frage."}), 400
    if not latest_approved_request(sid):
        return jsonify({"blocked": True, "error": "KI ist noch nicht durch die Lehrkraft freigegeben."}), 403
    usage = get_token_usage()
    estimated = estimate_tokens(message, format_protocol(sid))
    if usage["remaining"] < estimated:
        socketio.emit("ki_gesperrt", {"gesperrt": True, "reason": "Token-Limit erreicht."}, to=f"schueler_{sid}")
        return jsonify({"blocked": True, "error": "Das heutige Token-Limit ist erreicht."}), 429

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY ist auf dem Server nicht gesetzt."}), 500
    try:
        answer = call_gemini(api_key, sid, message)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    used = estimate_tokens(message, answer, format_protocol(sid))
    add_tokens(used)
    timestamp = now_iso()
    with get_db() as db:
        db.execute(
            "INSERT INTO chat_messages (schueler_id, rolle, inhalt, tokens, created_at) VALUES (?, 'user', ?, 0, ?)",
            (sid, message, timestamp),
        )
        db.execute(
            "INSERT INTO chat_messages (schueler_id, rolle, inhalt, tokens, created_at) VALUES (?, 'assistant', ?, ?, ?)",
            (sid, answer, used, now_iso()),
        )
        db.commit()
    socketio.emit("chat_live", {"id": sid, "role": "user", "content": message, "created_at": timestamp}, to=f"watch_{sid}")
    socketio.emit("chat_live", {"id": sid, "role": "assistant", "content": answer, "tokens": used}, to=f"watch_{sid}")
    return jsonify({"answer": answer, "tokens": used, "usage": get_token_usage()})


@app.post("/api/teacher/ki/<request_id>/<decision>")
def decide_ki(request_id, decision):
    if not require_teacher_action():
        return jsonify({"error": "Nicht erlaubt."}), 403
    if decision not in {"approve", "deny"}:
        return jsonify({"error": "Ungültige Entscheidung."}), 400
    status = "approved" if decision == "approve" else "denied"
    timestamp = now_iso()
    with get_db() as db:
        row = db.execute("SELECT * FROM ki_anfragen WHERE id=?", (request_id,)).fetchone()
        if not row:
            return jsonify({"error": "Anfrage nicht gefunden."}), 404
        db.execute("UPDATE ki_anfragen SET status=?, decided_at=? WHERE id=?", (status, timestamp, request_id))
        db.commit()
    socketio.emit("ki_entscheidung", {"id": request_id, "entscheid": status, "typ": decision}, to=f"schueler_{row['schueler_id']}")
    socketio.emit("ki_request_update", {"id": request_id, "status": status, "decided_at": timestamp}, to="lehrer_room")
    return jsonify({"ok": True, "status": status})


@app.post("/api/teacher/reset")
def reset_classroom():
    if not require_teacher_action():
        return jsonify({"error": "Nicht erlaubt."}), 403
    with get_db() as db:
        db.execute("DELETE FROM chat_messages")
        db.execute("DELETE FROM ki_anfragen")
        db.execute("DELETE FROM antworten")
        db.execute("DELETE FROM fortschritt")
        db.execute("DELETE FROM setup_images")
        db.execute("DELETE FROM schueler")
        db.execute("DELETE FROM token_usage")
        db.commit()
    for file_path in UPLOAD_DIR.glob("*"):
        if file_path.is_file():
            file_path.unlink()
    socketio.emit("classroom_reset", {}, to="lehrer_room")
    return jsonify({"ok": True})


@app.get("/api/teacher/snapshot")
def api_teacher_snapshot():
    if not teacher_required():
        return jsonify({"error": "Nicht angemeldet."}), 401
    return jsonify(student_snapshot())


def call_gemini(api_key, sid, message):
    model = quote(GEMINI_MODEL, safe="")
    url = GEMINI_ENDPOINT.format(model=model, key=quote(api_key, safe=""))
    prompt = (
        "Systemauftrag: Du bist ein freundlicher naturwissenschaftlicher KI-Tutor fuer ein "
        "Versuchsprotokoll an der Gesamtschule Meiderich. Fuehre das Gespraech sokratisch: "
        "Nimm dem Schueler nicht die Denkarbeit ab, sondern hilf ihm, den naechsten eigenen "
        "Gedankenschritt zu finden.\n\n"
        "Arbeitsweise:\n"
        "- Stelle zuerst eine kurze Rueckfrage oder einen Denkimpuls, wenn die Frage noch "
        "unklar ist oder der Schueler direkt nach einer Loesung fragt.\n"
        "- Gib keine fertigen Formulierungen fuer ganze Protokollfelder und keine "
        "Komplettloesungen. Vermeide Antworten, die der Schueler nur abschreiben kann.\n"
        "- Gib maximal zwei konkrete Hinweise pro Antwort und schliesse mit einem kleinen "
        "Arbeitsauftrag oder einer Frage, die der Schueler selbst beantworten muss.\n"
        "- Wenn etwas fachlich falsch wirkt, benenne den Denkfehler behutsam und frage nach "
        "Beobachtung, Messwert oder Begruendung.\n"
        "- Bei Sicherheitsfragen, Gefahrenstoffen oder Entsorgung gib klare, direkte Hinweise "
        "und verweise auf die Lehrkraft.\n"
        "- Antworte auf Deutsch, knapp und ermutigend. Nutze fuer Formeln saubere "
        "LaTeX-Schreibweise mit $...$.\n\n"
        f"Aktueller Stand des Protokolls:\n{format_protocol(sid)}\n\n"
        f"Schuelerfrage:\n{message}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.35, "topP": 0.9, "maxOutputTokens": 700},
    }
    api_request = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(api_request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google API Fehler ({exc.code}): {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Google API nicht erreichbar: {exc.reason}") from exc
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Google API lieferte keine auswertbare Antwort.") from exc


def save_setup_image(sid, data_url, file_name):
    match = re.match(r"^data:(image/(?:png|jpeg|jpg));base64,(.+)$", data_url, re.IGNORECASE)
    if not match:
        return
    mime_type = match.group(1).lower().replace("image/jpg", "image/jpeg")
    raw = base64.b64decode(match.group(2), validate=True)
    if len(raw) > 10 * 1024 * 1024:
        return
    ext = ".png" if mime_type == "image/png" else ".jpg"
    image_path = UPLOAD_DIR / f"{sid}{ext}"
    image_path.write_bytes(raw)
    with get_db() as db:
        db.execute(
            """
            INSERT INTO setup_images (schueler_id, file_path, file_name, mime_type, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(schueler_id) DO UPDATE SET
                file_path=excluded.file_path,
                file_name=excluded.file_name,
                mime_type=excluded.mime_type,
                updated_at=excluded.updated_at
            """,
            (sid, str(image_path), safe_filename(file_name), mime_type, now_iso()),
        )
        db.commit()


def safe_filename(value):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return cleaned.strip("._")[:100] or "Versuchsprotokoll.pdf"


def latest_answers(answers):
    by_task = {}
    for row in sorted(answers, key=lambda item: item["created_at"]):
        by_task[row["aufgabe"]] = row["inhalt"]
    return by_task


def build_protocol_pdf(snapshot):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.7 * cm,
        bottomMargin=1.6 * cm,
        title="Versuchsprotokoll",
    )
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Heading1", "Heading2", "BodyText", "Normal"):
        styles[style_name].fontName = PDF_FONT
    styles.add(ParagraphStyle(name="CenterTitle", parent=styles["Title"], fontName=PDF_FONT_BOLD, alignment=TA_CENTER, fontSize=18, leading=22, spaceAfter=10))
    styles.add(ParagraphStyle(name="SectionTitle", parent=styles["Heading2"], fontName=PDF_FONT_BOLD, fontSize=12, leading=15, textColor=colors.HexColor("#006AB3"), spaceBefore=10, spaceAfter=5))
    styles.add(ParagraphStyle(name="BodyClean", parent=styles["BodyText"], fontName=PDF_FONT, fontSize=10.5, leading=14, spaceAfter=8))
    styles.add(ParagraphStyle(name="Meta", parent=styles["BodyText"], fontName=PDF_FONT, fontSize=9, leading=12, textColor=colors.HexColor("#64748b")))

    student = snapshot["student"]
    answers = latest_answers(snapshot["answers"])
    story = [
        Paragraph("Interaktives Versuchsprotokoll", styles["CenterTitle"]),
        Paragraph(
            f"<b>Pseudonym:</b> {html.escape(student['pseudonym'])} &nbsp;&nbsp; "
            f"<b>Kurs:</b> {html.escape(student['kurs'])} &nbsp;&nbsp; "
            f"<b>Export:</b> {html.escape(dt.datetime.now().strftime('%d.%m.%Y %H:%M'))}",
            styles["Meta"],
        ),
        Spacer(1, 0.25 * cm),
    ]

    overview_rows = [["Feld", "Status"]]
    for task in TASKS:
        overview_rows.append([task["title"], "ausgefüllt" if answers.get(task["id"]) else "offen"])
    table = Table(overview_rows, colWidths=[10.8 * cm, 4.0 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006AB3")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), PDF_FONT),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dce4ee")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([table, Spacer(1, 0.25 * cm)])

    for index, task in enumerate(TASKS, start=1):
        story.append(Paragraph(f"{index}. {html.escape(task['title'])}", styles["SectionTitle"]))
        text = answers.get(task["id"], "")
        story.append(Paragraph(html.escape(text).replace("\n", "<br/>") if text else "<i>Nicht ausgefüllt.</i>", styles["BodyClean"]))
        if task["id"] == "setup" and snapshot.get("setup_image"):
            image_path = Path(snapshot["setup_image"]["file_path"])
            if image_path.exists():
                story.append(Spacer(1, 0.1 * cm))
                story.append(Image(str(image_path), width=10.5 * cm, height=7.0 * cm, kind="proportional"))
                story.append(Paragraph(html.escape(snapshot["setup_image"]["file_name"]), styles["Meta"]))

    doc.build(story, onFirstPage=pdf_footer, onLaterPages=pdf_footer)
    buffer.seek(0)
    return buffer


def pdf_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(PDF_FONT, 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(1.8 * cm, 1.0 * cm, "GSM Duisburg - Versuchsprotokoll")
    canvas.drawRightString(A4[0] - 1.8 * cm, 1.0 * cm, f"Seite {doc.page}")
    canvas.restoreState()


@socketio.on("schueler_join")
def on_schueler_join(data=None):
    sid = session.get("schueler_id")
    if not sid:
        return
    join_room(f"schueler_{sid}")
    with get_db() as db:
        db.execute("UPDATE schueler SET socket_id=?, last_active=? WHERE id=?", (request.sid, now_iso(), sid))
        db.commit()
    socketio.emit("schueler_online", {"id": sid}, to="lehrer_room")


@socketio.on("lehrer_join")
def on_lehrer_join():
    if not teacher_required():
        return
    join_room("lehrer_room")
    emit("dashboard_snapshot", student_snapshot())


@socketio.on("watch_schueler")
def on_watch_schueler(data):
    if not teacher_required():
        return
    sid = str((data or {}).get("schueler_id", ""))
    if sid:
        join_room(f"watch_{sid}")
        emit("detail_snapshot", detail_snapshot(sid))


@socketio.on("disconnect")
def on_disconnect():
    sid = session.get("schueler_id")
    if not sid:
        return
    with get_db() as db:
        db.execute("UPDATE schueler SET socket_id=NULL, last_active=? WHERE id=?", (now_iso(), sid))
        db.commit()
    socketio.emit("schueler_offline", {"id": sid}, to="lehrer_room")


if __name__ == "__main__":
    socketio.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        allow_unsafe_werkzeug=True,
    )
