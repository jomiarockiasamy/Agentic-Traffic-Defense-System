"""Flask app: ticketing site + defense APIs. Run: python3 app.py (or: PORT=5001 python3 app.py)"""
import os
import random
import sqlite3
import time

from flask import Flask, Response, jsonify, redirect, render_template_string, request, session, g
from werkzeug.security import check_password_hash, generate_password_hash

from defender.runtime import client_ip_for_defense, defender

app = Flask(__name__)
app.secret_key = "demo-secret-key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
TICKET_CAP = 100

# Demo degradation mode (simulated latency)
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


def current_dynamic_delay(active_count: int) -> float:
    if active_count <= DEMO_CAPACITY:
        return DEMO_BASE_DELAY
    overload = active_count - DEMO_CAPACITY
    scaled = DEMO_BASE_DELAY + overload * DEMO_DELAY_PER_USER
    return min(scaled, DEMO_MAX_DYNAMIC_DELAY)


@app.before_request
def traffic_and_defense_layer():
    blocked = defender.before_request(
        client_ip=client_ip_for_defense(request.headers, request.remote_addr),
    )
    if blocked is not None:
        return blocked

    # Keep degrade-mode snapshot for the latency simulator.
    g.active_requests_snapshot = defender.active_requests

    if not DEMO_MODE:
        return None
    if request.path not in DEMO_DELAY_PATHS:
        return None
    dynamic_delay = current_dynamic_delay(g.active_requests_snapshot)
    jitter_min = max(0.0, dynamic_delay * 0.8)
    jitter_max = dynamic_delay * 1.2
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
    defender.after_request()
    return response


def page(name: str, **ctx):
    with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
        defaults = defender.template_context()
        defaults.update(ctx)
        return render_template_string(f.read(), **defaults)


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


@app.get("/state")
def state():
    return jsonify(defender.build_state())


@app.get("/debug/client-ip")
def debug_client_ip():
    """Verify X-Forwarded-For vs TCP remote_addr (Locust sends one TCP IP; XFF is the simulated client)."""
    return jsonify(
        {
            "effective_client_ip": client_ip_for_defense(request.headers, request.remote_addr),
            "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            "remote_addr": request.remote_addr,
            "trust_x_forwarded_for": os.getenv("TRUST_X_FORWARDED_FOR", "1") == "1",
        }
    )


@app.get("/monitor")
def monitor_page():
    with open(os.path.join(BASE_DIR, "monitor.html"), encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.get("/traffic")
def traffic():
    # Back-compat endpoint (most dashboards should use /state).
    s = defender.build_state()
    allowed = s["traffic"]["allowed_rps_series"]
    blocked = s["traffic"]["blocked_rps_series"]
    now = int(time.time())
    start = now - (defender.METRICS_WINDOW_SECONDS - 1)
    per_second = [
        {"ts": start + i, "rps": allowed[i] + blocked[i], "allowed": allowed[i], "blocked": blocked[i]}
        for i in range(defender.METRICS_WINDOW_SECONDS)
    ]
    return jsonify(
        {
            "request_count": defender.request_count,
            "allowed_count": defender.allowed_count,
            "blocked_count": defender.blocked_count,
            "active_requests": defender.active_requests,
            "window_seconds": defender.METRICS_WINDOW_SECONDS,
            "defense": {"max_rps": defender.max_rps},
            "per_second": per_second,
        }
    )


@app.post("/tools/set_max_rps")
def http_set_max_rps():
    body = request.get_json(silent=True) or {}
    value = body.get("value")
    if value is None:
        return jsonify({"ok": False, "error": "missing value"}), 400
    try:
        res = defender.tool_set_max_rps(int(value))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    defender.tool_actions.append({"ts": int(time.time()), "results": [{"tool": "set_max_rps", **res}]})
    return jsonify(res)


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=True, port=port)
