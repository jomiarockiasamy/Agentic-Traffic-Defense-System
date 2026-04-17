import os
import random
import sqlite3
import time
from threading import Lock

from flask import Flask, g, redirect, render_template_string, request, session
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "demo-secret-key"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
TICKET_CAP = 100
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"
DEMO_DELAY_MIN = float(os.getenv("DEMO_DELAY_MIN", "0.08"))
DEMO_DELAY_MAX = float(os.getenv("DEMO_DELAY_MAX", "8"))
DEMO_BASE_DELAY = float(os.getenv("DEMO_BASE_DELAY", "0.1"))
DEMO_CAPACITY = int(os.getenv("DEMO_CAPACITY", "8"))
DEMO_DELAY_PER_USER = float(os.getenv("DEMO_DELAY_PER_USER", "0.35"))
DEMO_MAX_DYNAMIC_DELAY = float(os.getenv("DEMO_MAX_DYNAMIC_DELAY", "12"))
DEMO_DELAY_PATHS = {
    "/auth",
    "/signup",
    "/login",
    "/dashboard",
    "/concert1",
    "/concert2",
    "/concert3",
    "/buy/concert1",
    "/buy/concert2",
    "/buy/concert3",
}
active_requests = 0
active_requests_lock = Lock()


def current_dynamic_delay(active_count: int) -> float:
    if active_count <= DEMO_CAPACITY:
        return DEMO_BASE_DELAY
    overload = active_count - DEMO_CAPACITY
    scaled = DEMO_BASE_DELAY + overload * DEMO_DELAY_PER_USER
    return min(scaled, DEMO_MAX_DYNAMIC_DELAY)


@app.before_request
def demo_degrade_mode():
    global active_requests
    with active_requests_lock:
        active_requests += 1
        g.active_requests_snapshot = active_requests
    if not DEMO_MODE:
        return None
    if request.path not in DEMO_DELAY_PATHS:
        return None
    dynamic_delay = current_dynamic_delay(g.active_requests_snapshot)
    jitter_min = dynamic_delay * 0.8
    jitter_max = dynamic_delay * 1.2
    jitter_min = max(0.0, jitter_min)
    if DEMO_DELAY_MIN > 0:
        jitter_min = max(jitter_min, DEMO_DELAY_MIN)
    if DEMO_DELAY_MAX > 0:
        jitter_max = min(jitter_max, DEMO_DELAY_MAX)
    if jitter_max < jitter_min:
        jitter_max = jitter_min
    time.sleep(random.uniform(jitter_min, jitter_max))
    return None


@app.after_request
def release_active_request(response):
    global active_requests
    with active_requests_lock:
        active_requests = max(0, active_requests - 1)
    return response


def page(name: str, **ctx):
    with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
        return render_template_string(f.read(), **ctx)


def ticket_remaining():
    conn = db_conn()
    remaining = {}
    for concert_id in ("concert1", "concert2", "concert3"):
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS sold FROM user_tickets WHERE concert_id = ?",
            (concert_id,),
        ).fetchone()
        remaining[concert_id] = TICKET_CAP - int(row["sold"])
    conn.close()
    return remaining


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS concerts (
            concert_id TEXT PRIMARY KEY,
            ticket_cap INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_tickets (
            user_id INTEGER NOT NULL,
            concert_id TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, concert_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO concerts (concert_id, ticket_cap) VALUES
        ('concert1', 100),
        ('concert2', 100),
        ('concert3', 100)
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS ticket_cap_insert
        BEFORE INSERT ON user_tickets
        BEGIN
            SELECT CASE
                WHEN (
                    COALESCE((SELECT SUM(quantity) FROM user_tickets WHERE concert_id = NEW.concert_id), 0)
                    + NEW.quantity
                ) > COALESCE((SELECT ticket_cap FROM concerts WHERE concert_id = NEW.concert_id), 0)
                THEN RAISE(ABORT, 'ticket_cap_exceeded')
            END;
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS ticket_cap_update
        BEFORE UPDATE OF quantity ON user_tickets
        BEGIN
            SELECT CASE
                WHEN (
                    COALESCE((SELECT SUM(quantity) FROM user_tickets WHERE concert_id = NEW.concert_id), 0)
                    - OLD.quantity
                    + NEW.quantity
                ) > COALESCE((SELECT ticket_cap FROM concerts WHERE concert_id = NEW.concert_id), 0)
                THEN RAISE(ABORT, 'ticket_cap_exceeded')
            END;
        END
        """
    )
    conn.commit()
    conn.close()


@app.get("/")
def home():
    return page(
        "home.html",
        user=session.get("user"),
        msg=request.args.get("msg", ""),
        remaining=ticket_remaining(),
    )

@app.get("/auth")
def auth_page():
    if session.get("user"):
        return redirect("/dashboard")
    return page("auth.html", msg=request.args.get("msg", ""))

@app.post("/signup")
def signup():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not name or not email or not password:
        return redirect("/auth?msg=Fill+all+signup+fields")
    conn = db_conn()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        return redirect("/auth?msg=Account+already+exists")
    conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        (name, email, generate_password_hash(password, method="pbkdf2:sha256")),
    )
    conn.commit()
    conn.close()
    session["user"] = name
    session["user_email"] = email
    return redirect("/dashboard")

@app.post("/login")
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    conn = db_conn()
    user = conn.execute("SELECT name, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not user or not check_password_hash(user["password_hash"], password):
        return redirect("/auth?msg=Invalid+login")
    session["user"] = user["name"]
    session["user_email"] = email
    return redirect("/dashboard")

@app.get("/dashboard")
def dashboard():
    if not session.get("user"):
        return redirect("/?msg=Please+log+in")
    return page("dashboard.html", user=session.get("user"), remaining=ticket_remaining())

@app.get("/concert1")
def concert1():
    if not session.get("user"):
        return redirect("/?msg=Please+log+in")
    return page("concert1.html", msg=request.args.get("msg", ""))

@app.get("/concert2")
def concert2():
    if not session.get("user"):
        return redirect("/?msg=Please+log+in")
    return page("concert2.html", msg=request.args.get("msg", ""))

@app.get("/concert3")
def concert3():
    if not session.get("user"):
        return redirect("/?msg=Please+log+in")
    return page("concert3.html", msg=request.args.get("msg", ""))

@app.post("/buy/<concert_id>")
def buy(concert_id):
    if not session.get("user"):
        return redirect("/?msg=Please+log+in")
    user_email = session.get("user_email")
    if not user_email:
        return redirect("/auth?msg=Please+log+in+again")
    if concert_id not in {"concert1", "concert2", "concert3"}:
        return redirect("/dashboard")
    qty_raw = request.form.get("quantity", "1")
    try:
        qty = int(qty_raw)
    except ValueError:
        return redirect(f"/{concert_id}?msg=Invalid+ticket+quantity")
    if qty < 1:
        return redirect(f"/{concert_id}?msg=Invalid+ticket+quantity")
    conn = db_conn()
    sold_row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS sold FROM user_tickets WHERE concert_id = ?",
        (concert_id,),
    ).fetchone()
    remaining = TICKET_CAP - int(sold_row["sold"])
    if remaining <= 0:
        conn.close()
        return redirect(f"/{concert_id}?msg=Sold+out")
    if qty > remaining:
        conn.close()
        return redirect(f"/{concert_id}?msg=Only+{remaining}+ticket(s)+left")
    user_row = conn.execute("SELECT id FROM users WHERE email = ?", (user_email,)).fetchone()
    if not user_row:
        conn.close()
        return redirect("/auth?msg=Please+log+in+again")
    try:
        conn.execute(
            """
            INSERT INTO user_tickets (user_id, concert_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, concert_id)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (user_row["id"], concert_id, qty),
        )
        conn.commit()
        left_after_buy = remaining - qty
        conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        return redirect(f"/{concert_id}?msg=Sold+out")
    if left_after_buy == 0:
        return redirect(f"/{concert_id}?msg=Order+placed.+Sold+out+now")
    return redirect(f"/{concert_id}?msg=Order+placed+for+{qty}+ticket(s).+{left_after_buy}+left")

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/?msg=Logged+out")

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
