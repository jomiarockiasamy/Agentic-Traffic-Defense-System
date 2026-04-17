import ast
import os
import re
import subprocess
import sys
import json
import urllib.request
from typing import Optional

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileIconProvider,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QFileInfo, QUrl, QSize, Qt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)

_ENV_PATH = os.path.join(BASE_DIR, ".env")


def _load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs into os.environ (no extra deps). Skips # comments."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


try:
    from dotenv import load_dotenv

    load_dotenv(_ENV_PATH)
except ImportError:
    _load_env_file(_ENV_PATH)
else:
    # If user runs system python without dotenv, load_dotenv still ran; if key missing, try manual
    if not os.environ.get("GROQ_API_KEY", "").strip():
        _load_env_file(_ENV_PATH)
LOCUSTFILE = os.path.join(BASE_DIR, "locustfile.py")
LOCUST_BIN = os.path.join(ROOT_DIR, ".venv", "bin", "locust")
LOG_PATH = os.path.join(BASE_DIR, "locust-gui.log")
PLAN_PATH = os.path.join(BASE_DIR, "approved_plan.json")
PLANS_DIR = os.path.join(BASE_DIR, "approved_plans")
CATALOG_PATH = os.path.join(BASE_DIR, "target_catalog.json")
DEFAULT_PROVIDER = "mock"
DEFAULT_MODEL = "demo-planner-v1"
# Groq exposes an OpenAI-compatible HTTP API; we use the official `openai` Python package as client.
GROQ_OPENAI_BASE = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
LLM_PROVIDER = "groq"

PLANNER_SYSTEM = """You are a safe load-test planner for a demo web app the operator owns.
Return ONE JSON object only (no markdown). Required top-level keys:
  persona (string),
  spawn_rate (integer, suggested 2-15 for a laptop demo),
  duration (string, Locust headless time like "2m" or "90s"),
  think_time_min (number, seconds),
  think_time_max (number, seconds, must be >= think_time_min),
  actions (array).

Optional top-level key (use when the operator asks for a message, greeting, or "comment"):
  operator_note (string) — e.g. if they ask to "say hello at the end", set "operator_note": "hello".
  Standard JSON cannot contain // comments; put any such text here instead.

Each element of actions must have:
  name (string) — MUST be one of the values in the catalog's actions_allowlist.
  weight (integer, >= 1) — relative task frequency for Locust.
  params (object, optional) — ONLY for view_concert or buy_ticket.

For view_concert and buy_ticket, params may include:
  concert_ids: array of strings — MUST be a subset of catalog.entity_ids.concert_ids (may be full list).
For buy_ticket only, params may include:
  qty_weights: array of numbers, same length as catalog.constraints.allowed_buy_quantities,
    non-negative, will be normalized to probabilities for buying quantity 1, 2, 3 in that order.

Do NOT add catalog or endpoints yourself (the app merges the real catalog). Do NOT invent action names.
Follow the operator's extra instructions when they do not conflict with safety or the allowlist."""

LOCUSTFILE_GEN_SYSTEM = """You are an expert Python Locust load-test author for a demo site the operator owns.
Output ONLY the complete Python source for one file: locustfile.py. Valid Python 3.9+, runnable as: locust -f locustfile.py
MANDATORY — Locust 2.x API ONLY:
  - Use: from locust import HttpUser, task, between (and any other current locust imports).
  - User class MUST inherit HttpUser (never HttpLocust — removed in Locust 1.0).
  - Do NOT use TaskSet, task_set, min_wait, max_wait, or Locust 0.x patterns.
  - Use @task and wait_time = between(...) on the HttpUser class.
  - Prefer ONE HttpUser class with weighted @task methods. If you define several HttpUser subclasses, Locust runs ALL of them together — do not add extra user classes "for completeness" that should not run; merge into one class or use task weights only.
  - For HTML form POSTs use self.client.post(path, data={...}) with the data= keyword (form-encoded), not JSON bodies unless the contract says JSON.

HARD-CODED DEMO APP (Flask) — request shapes are ALWAYS as below; follow these even if the JSON contract is vague or an LLM might guess wrong field names:
  - POST /signup — required form fields: name (string), email (string, MUST be unique per request e.g. uuid), password (string). WRONG: username, concert_id-only, or omitting name/email/password. For name, use random.choice from a long list/tuple of display names in the file — do not use a single constant like "John Doe" for every request.
  - POST /login — form fields: email, password.
  - GET home: / ; GET auth: /auth ; GET dashboard: /dashboard (needs session).
  - GET concert pages: path is "/" + concert_id from entity_ids (e.g. /concert1, /concert2, /concert3) — not a bare "/concert_id" placeholder string.
  - POST /buy/<concert_id> — form field: quantity (integer 1, 2, or 3 per constraints). Requires an authenticated session; same HttpUser must signup or login first so cookies apply.
  - Session: use the same self.client for a virtual user so cookies from signup/login are sent on later requests.

If you use markdown, put all code in a single ```python fenced block. No explanatory prose outside the fence.
End the file with a short # ASSUMPTIONS: section as comments.
Note: Per-user source IP rotation is not available from a single Locust process without OS-level socket binding; if the prompt mentions different IPs, state that limitation in comments only — do not fake IPs in code."""


LOCUSTFILE_GEN_USER_TEMPLATE = r"""Generate a complete, self-contained locustfile.py for this traffic profile: {{BOT_TYPE}}.

Allowed traffic profiles:
- signup_only: creates accounts only and performs no further actions
- bulk_buyer: simulates high-volume but contract-compliant ticket purchasing activity
- casual_browser: simulates normal browsing behavior with occasional account creation or login if supported

Site contract:
__SITE_JSON__

Requirements:
- only use allowed actions and documented endpoint templates
- use the provided concert IDs as the test data pool
- enforce all constraints
- produce realistic, contract-compliant user-task sequencing
- include weighted Locust tasks and session handling (see system prompt: exact form field names for /signup and /login)
- include comments and minimal assumptions
- keep all behavior within normal staging or demo load-testing scope

Test data:
- For any action that needs a concert, choose concert_id only from entity_ids.concert_ids, or from the catalog's documented ID list if that is the same source
- Rotate or randomize across the pool so load is spread realistically
- Signup tasks MUST POST name, email, password (not username/concert_id); use import uuid (or similar) so each email is unique; pick name with random.choice from a long SIGNUP_NAMES list in the file so bots do not all share one display name

Constraints:
- Honor every key under constraints, including caps, allowed quantities, cooldowns, and allowed parameter sets
- Do not exceed ticket quantities or use values outside allowed sets
- If login or signup is required before another action, respect that order
- If the contract is missing a required endpoint template for an allowlisted action, do not invent one; instead note the issue clearly in code comments and skip that flow

Safety and scope:
- This is a local or staging load test for a demo site I control
- Do not add destructive, evasive, or out-of-contract behavior
- Do not call URLs not derivable from the JSON contract
- Use different IPs: not achievable per virtual user in plain Locust; document in comments if relevant
- Do not simulate credential attacks, fraud patterns, stealth behavior, or attempts to bypass limits

Profile behavior guidance:

signup_only:
- create accounts only if signup is an allowed action
- implement ONLY signup traffic: one HttpUser class whose tasks only POST /signup with data=name,email,password as in the system prompt (no extra user classes for buy/browse)
- if signup is unavailable, note that in comments and avoid fallback behavior

bulk_buyer:
- simulate repeated but constraint-compliant purchase flows
- if account creation or login is required, do that first
- spread purchases across available concert IDs
- respect purchase limits, cooldowns, and all documented constraints

casual_browser:
- emphasize browse-oriented actions such as catalog view, concert detail view, and availability checks
- occasionally create an account or log in only if supported and relevant
- keep purchase activity rare or absent unless explicitly allowed by the selected profile and contract

Output requirements:
- Return only the full Python source for locustfile.py
- The file must be runnable with: locust -f locustfile.py
- Include all imports, helper functions, user classes, weighted tasks, and comments in one file
- After the code, include a very short assumptions section as Python comments at the end of the same file

Optional scaling logic:
- Prefer task weights on ONE HttpUser; multiple HttpUser subclasses all run in parallel — only use several classes if you truly want mixed traffic, not for "unused" placeholders
- Keep everything in a single locustfile.py unless the prompt explicitly asks for multiple files
- Do not create separate files solely by bot count unless explicitly requested
"""


def persona_to_bot_type(persona_label: str) -> str:
    return {
        "bulk buyer": "bulk_buyer",
        "casual buyer": "casual_browser",
        "signup bot": "signup_only",
    }.get(persona_label.strip(), "casual_browser")


def build_locustfile_user_prompt(bot_type: str, catalog: dict, extra: str) -> str:
    site_json = json.dumps(catalog, indent=2)
    text = LOCUSTFILE_GEN_USER_TEMPLATE.replace("{{BOT_TYPE}}", bot_type).replace("__SITE_JSON__", site_json)
    if extra.strip():
        text += "\n\nAdditional operator notes:\n" + extra.strip()
    return text


def extract_python_from_llm(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def validate_locustfile_source(code: str) -> tuple[bool, str]:
    """Return (ok, human_reason_if_bad). Rejects Locust 0.x patterns that break Locust 2.x."""
    if not code or not code.strip():
        return False, "The model returned no code (empty)."
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return False, f"Invalid Python syntax: {exc}"
    if "HttpLocust" in code:
        return (
            False,
            "Uses HttpLocust — that class was removed in Locust 1.0. Subclass HttpUser instead.",
        )
    if "task_set" in code.replace(" ", ""):
        return (
            False,
            "Uses Locust 0.x TaskSet / task_set — use @task methods on an HttpUser subclass in Locust 1+.",
        )
    if re.search(r"\bmin_wait\b", code) or re.search(r"\bmax_wait\b", code):
        return (
            False,
            "Uses min_wait / max_wait — in Locust 1+ use wait_time = between(a, b) on the user class.",
        )
    return True, ""


def build_test_plan_raw(catalog: dict, persona: str, extra_prompt: str) -> dict:
    """Minimal synthetic plan for PlannerWorker test mode (no HTTP). JSON has no // comments; use operator_note."""
    allow = catalog.get("actions_allowlist") or []
    concert_ids = catalog.get("entity_ids", {}).get("concert_ids", catalog.get("concert_ids", []))
    actions = []
    if "browse_home" in allow:
        actions.append({"name": "browse_home", "weight": 2})
    if "view_concert" in allow and concert_ids:
        actions.append(
            {"name": "view_concert", "weight": 2, "params": {"concert_ids": list(concert_ids)}}
        )
    if not actions and allow:
        actions.append({"name": allow[0], "weight": 1})
    tail = "hello — end-of-plan note (JSON cannot use // comments)"
    if extra_prompt.strip():
        tail = f"{tail} | operator: {extra_prompt.strip()[:200]}"
    return {
        "persona": persona,
        "spawn_rate": 5,
        "duration": "1m",
        "think_time_min": 0.1,
        "think_time_max": 0.35,
        "actions": actions,
        "operator_note": tail,
    }


def merge_llm_plan(
    raw: dict,
    catalog: dict,
    ui_persona: str,
    ui_model: str,
    provider: str = LLM_PROVIDER,
    api_base: Optional[str] = None,
    extra_prompt: str = "",
    plan_source: str = "groq_api",
) -> dict:
    allow = set(catalog.get("actions_allowlist", []))
    concert_ids = catalog.get("entity_ids", {}).get("concert_ids", catalog.get("concert_ids", []))
    allowed_qty = catalog.get("constraints", {}).get("allowed_buy_quantities", [1, 2, 3])
    if not isinstance(allowed_qty, list) or not allowed_qty:
        allowed_qty = [1, 2, 3]

    base = api_base or GROQ_OPENAI_BASE
    persona_s = (raw.get("persona") or ui_persona or "custom").strip()
    if plan_source == "test_json":
        plan = {
            "persona": persona_s,
            "plan_source": "test_json",
            "provider": "local_test",
            "model": ui_model,
            "llm": None,
            "spawn_rate": int(raw.get("spawn_rate", 8)),
            "duration": str(raw.get("duration", "2m")),
            "think_time_min": float(raw.get("think_time_min", 0.1)),
            "think_time_max": float(raw.get("think_time_max", 0.5)),
            "catalog": catalog,
            "actions": [],
        }
    else:
        plan = {
            "persona": persona_s,
            "plan_source": "groq_api",
            "provider": provider,
            "model": ui_model,
            "llm": {
                "backend": "groq",
                "api_base": base,
                "model_id": ui_model,
            },
            "spawn_rate": int(raw.get("spawn_rate", 8)),
            "duration": str(raw.get("duration", "2m")),
            "think_time_min": float(raw.get("think_time_min", 0.1)),
            "think_time_max": float(raw.get("think_time_max", 0.5)),
            "catalog": catalog,
            "actions": [],
        }
    for a in raw.get("actions", []):
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        if name not in allow:
            continue
        w = int(a.get("weight", 1))
        if w < 1:
            continue
        entry = {"name": name, "weight": w}
        if name in ("view_concert", "buy_ticket"):
            params = dict(a.get("params") or {})
            ids = [x for x in params.get("concert_ids", concert_ids) if x in concert_ids]
            if not ids:
                ids = list(concert_ids)
            entry["params"] = {"concert_ids": ids}
            if name == "buy_ticket":
                qw = params.get("qty_weights")
                n = len(allowed_qty)
                if isinstance(qw, list) and len(qw) == n and all(isinstance(x, (int, float)) for x in qw):
                    weights = [max(0.0, float(x)) for x in qw]
                else:
                    weights = [1.0 / n] * n
                s = sum(weights)
                if s <= 0:
                    weights = [1.0 / n] * n
                else:
                    weights = [x / s for x in weights]
                entry["params"]["qty_weights"] = weights
        plan["actions"].append(entry)

    if not plan["actions"]:
        raise ValueError("Model returned no actions that match the catalog allowlist.")

    plan["think_time_min"] = max(0.01, plan["think_time_min"])
    plan["think_time_max"] = max(plan["think_time_min"], plan["think_time_max"])
    plan["spawn_rate"] = max(1, min(plan["spawn_rate"], 50))

    note: Optional[str] = None
    for k in ("operator_note", "note", "planner_comment", "comment"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            note = v.strip()
            break
    if note:
        plan["operator_note"] = note
    elif extra_prompt.strip() and plan_source != "test_json":
        # Light fallback when the model ignores operator_note but the ask is obvious
        low = extra_prompt.lower()
        if (
            "that says hello" in low
            or "say hello" in low
            or "says hello" in low
            or 'says "hello"' in low
        ):
            plan["operator_note"] = "hello"

    return plan


class PlannerWorker(QThread):
    finished_ok = Signal(dict)
    finished_err = Signal(str)

    def __init__(
        self,
        api_key: str,
        model: str,
        catalog: dict,
        persona: str,
        extra_prompt: str,
        base_url: str = GROQ_OPENAI_BASE,
        test_json_only: bool = False,
    ):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.catalog = catalog
        self.persona = persona
        self.extra_prompt = extra_prompt
        self.base_url = base_url
        self.test_json_only = test_json_only

    def run(self):
        if self.test_json_only:
            try:
                raw = build_test_plan_raw(self.catalog, self.persona, self.extra_prompt)
                plan = merge_llm_plan(
                    raw,
                    self.catalog,
                    self.persona,
                    self.model,
                    LLM_PROVIDER,
                    None,
                    self.extra_prompt,
                    plan_source="test_json",
                )
                self.finished_ok.emit(plan)
            except Exception as exc:
                self.finished_err.emit(str(exc))
            return
        try:
            from openai import OpenAI
        except ImportError as exc:
            vpy = os.path.join(ROOT_DIR, ".venv", "bin", "python")
            launcher = os.path.join(BASE_DIR, "run_gui.sh")
            self.finished_err.emit(
                "The `openai` Python package is required (Groq uses the OpenAI-compatible API).\n\n"
                "Fix one of:\n"
                f"  • Run: {launcher}\n"
                f"  • Or:  {vpy} {os.path.join(BASE_DIR, 'locust_control_gui.py')}\n"
                "  • Or install: python3 -m pip install --user 'openai>=1.40.0'\n\n"
                f"({exc})"
            )
            return
        user_parts = [
            "Attack-surface catalog (follow strictly):\n",
            json.dumps(self.catalog, indent=2),
            f'\nSelected persona label: {self.persona}\n',
            "Extra instructions from operator:\n",
            (self.extra_prompt.strip() or "(none)"),
        ]
        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            resp = client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                temperature=0.35,
                messages=[
                    {"role": "system", "content": PLANNER_SYSTEM},
                    {"role": "user", "content": "".join(user_parts)},
                ],
            )
            text = resp.choices[0].message.content
            raw = json.loads(text)
            plan = merge_llm_plan(
                raw,
                self.catalog,
                self.persona,
                self.model,
                LLM_PROVIDER,
                self.base_url,
                self.extra_prompt,
                plan_source="groq_api",
            )
            self.finished_ok.emit(plan)
        except Exception as exc:
            self.finished_err.emit(str(exc))


class LocustfileGenWorker(QThread):
    """Calls Groq to emit a full locustfile.py from LOCUSTFILE_GEN_* prompt + site contract."""

    finished_ok = Signal(str)
    finished_err = Signal(str, str)

    def __init__(
        self,
        api_key: str,
        model: str,
        catalog: dict,
        bot_type: str,
        extra: str,
        base_url: str = GROQ_OPENAI_BASE,
    ):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.user_content = build_locustfile_user_prompt(bot_type, catalog, extra)

    def run(self):
        try:
            from openai import OpenAI
        except ImportError as exc:
            self.finished_err.emit(str(exc), "")
            return
        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            resp = client.chat.completions.create(
                model=self.model,
                temperature=0.25,
                messages=[
                    {"role": "system", "content": LOCUSTFILE_GEN_SYSTEM},
                    {"role": "user", "content": self.user_content},
                ],
            )
            text = resp.choices[0].message.content or ""
            code = extract_python_from_llm(text)
            if not code.strip():
                self.finished_err.emit("Model returned empty code.", "")
                return
            ok, reason = validate_locustfile_source(code)
            if not ok:
                self.finished_err.emit(
                    "Generated code failed Locust 2.x validation:\n\n"
                    f"{reason}\n\n"
                    f"{LOCUSTFILE} was not changed. The code below is shown for editing; regenerate after fixing.",
                    code,
                )
                return
            with open(LOCUSTFILE, "w", encoding="utf-8") as f:
                f.write(code)
            self.finished_ok.emit(code)
        except Exception as exc:
            self.finished_err.emit(str(exc), "")


def bot_slug(persona: str) -> str:
    return "".join(ch for ch in persona.lower() if ch.isalnum())


def next_plan_name(persona: str) -> str:
    slug = bot_slug(persona)
    os.makedirs(PLANS_DIR, exist_ok=True)
    max_n = 0
    for name in os.listdir(PLANS_DIR):
        if not name.endswith(".json"):
            continue
        if not name.startswith(slug):
            continue
        num_part = name[len(slug):-5]
        if num_part.isdigit():
            max_n = max(max_n, int(num_part))
    return f"{slug}{max_n + 1}.json"


class LocustControlWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Locust Bot Control")
        self.resize(900, 650)

        self.process = None
        self.log_file = None
        self.icon_provider = QFileIconProvider()
        self._planner_worker = None
        self._locustfile_worker = None

        self.persona_select = QComboBox()
        self.persona_select.addItems(["bulk buyer", "casual buyer", "signup bot"])
        self.model_input = QLineEdit(GROQ_DEFAULT_MODEL)
        self.host_input = QLineEdit("http://127.0.0.1:5000")
        self.users_input = QLineEdit("60")
        self.status_label = QLabel("Stopped")
        self._catalog_fetch_timer = QTimer(self)
        self._catalog_fetch_timer.setSingleShot(True)
        self._catalog_fetch_timer.timeout.connect(self._fetch_catalog_debounced)
        self.plan_box = QTextEdit()
        self.log_box = QTextEdit()
        self.plan_box.setPlaceholderText("Generated plan JSON appears here...")
        self.log_box.setReadOnly(True)

        self.stack = QStackedWidget()
        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1500)
        self.host_input.textChanged.connect(self._schedule_catalog_fetch)

    def _schedule_catalog_fetch(self):
        self._catalog_fetch_timer.start(600)

    def _fetch_catalog_debounced(self):
        self.connect_target(silent=True)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.addWidget(self.stack)

        # Main control page
        main_page = QWidget()
        main_outer = QVBoxLayout(main_page)

        form = QGridLayout()
        form.addWidget(QLabel("Target Host"), 0, 0)
        form.addWidget(self.host_input, 0, 1, 1, 3)

        form.addWidget(QLabel("Groq Model"), 1, 0)
        form.addWidget(self.model_input, 1, 1)
        form.addWidget(QLabel("Users"), 1, 2)
        form.addWidget(self.users_input, 1, 3)

        form.addWidget(QLabel("Persona"), 2, 0)
        form.addWidget(self.persona_select, 2, 1)
        form.addWidget(QLabel("Status"), 2, 2)
        form.addWidget(self.status_label, 2, 3)
        main_outer.addLayout(form)

        buttons = QHBoxLayout()
        self.generate_btn = QPushButton("Generate Plan")
        self.gen_locustfile_btn = QPushButton("Generate locustfile.py (LLM)")
        approve_btn = QPushButton("Approve Plan")
        start_btn = QPushButton("Start Bots")
        stop_btn = QPushButton("Stop Bots")
        show_plans_btn = QPushButton("Show Approved Plans")
        self.generate_btn.clicked.connect(self.generate_plan)
        self.gen_locustfile_btn.clicked.connect(self.generate_locustfile)
        approve_btn.clicked.connect(self.approve_plan)
        start_btn.clicked.connect(self.start_bots)
        stop_btn.clicked.connect(self.stop_bots)
        show_plans_btn.clicked.connect(self.show_approved_plans_view)
        buttons.addWidget(self.generate_btn)
        buttons.addWidget(self.gen_locustfile_btn)
        buttons.addWidget(approve_btn)
        buttons.addWidget(start_btn)
        buttons.addWidget(stop_btn)
        buttons.addWidget(show_plans_btn)
        main_outer.addLayout(buttons)

        main_outer.addWidget(QLabel("Reviewable Action Plan"))
        self.plan_box.setMinimumHeight(180)
        main_outer.addWidget(self.plan_box)
        main_outer.addWidget(QLabel("Locust Output"))
        self.log_box.setMinimumHeight(220)
        main_outer.addWidget(self.log_box)

        # Approved plans browser page
        plans_page = QWidget()
        plans_outer = QVBoxLayout(plans_page)
        plans_head = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        plans_head.addWidget(back_btn)
        plans_head.addWidget(QLabel("Approved Plans"))
        plans_head.addStretch(1)
        plans_outer.addLayout(plans_head)

        self.plans_list = QListWidget()
        self.plans_list.setViewMode(QListWidget.IconMode)
        self.plans_list.setResizeMode(QListWidget.Adjust)
        self.plans_list.setIconSize(QSize(72, 72))
        self.plans_list.setGridSize(QSize(170, 120))
        self.plans_list.setSpacing(10)
        self.plans_list.setMovement(QListWidget.Static)
        self.plans_list.setWordWrap(True)
        self.plans_list.setTextElideMode(Qt.ElideMiddle)
        self.plans_list.itemDoubleClicked.connect(self.open_selected_plan)
        self.plans_list.itemActivated.connect(self.open_selected_plan)
        plans_outer.addWidget(self.plans_list)

        self.stack.addWidget(main_page)
        self.stack.addWidget(plans_page)
        self.stack.setCurrentIndex(0)
        QTimer.singleShot(150, lambda: self.connect_target(silent=True))

    def connect_target(self, silent: bool = False):
        host = self.host_input.text().strip().rstrip("/")
        if not host or not host.startswith(("http://", "https://")):
            return
        url = f"{host}/api/attack-surface"
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            with open(CATALOG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if not silent:
                QMessageBox.information(self, "Connected", f"Fetched attack-surface catalog from {url}")
        except Exception as exc:
            QMessageBox.critical(self, "Connect Failed", f"Could not fetch {url}\n\n{exc}")

    def _set_plan_generating(self, active: bool):
        self.generate_btn.setEnabled(not active)
        self.gen_locustfile_btn.setEnabled(not active)
        if active:
            self.status_label.setText("Generating plan…")

    def _on_planner_ok(self, plan: dict):
        self.plan_box.setPlainText(json.dumps(plan, indent=2))
        src = plan.get("plan_source")
        if src == "groq_api":
            self.status_label.setText("Plan from Groq — review, then Approve")
        elif src == "test_json":
            self.status_label.setText("Test plan from PlannerWorker — review, then Approve")

    def _on_planner_err(self, message: str):
        QMessageBox.critical(self, "Groq planner failed", message)

    def _on_planner_finished(self):
        self._planner_worker = None
        self._set_plan_generating(False)
        self._tick()

    def generate_plan(self):
        if self._planner_worker is not None or self._locustfile_worker is not None:
            return
        if not os.path.exists(CATALOG_PATH):
            QMessageBox.warning(
                self,
                "No catalog",
                "Target catalog not loaded yet. Edit Target Host (valid URL) and wait a moment, or ensure the demo is running.",
            )
            return
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            QMessageBox.warning(
                self,
                "API key needed",
                "Set GROQ_API_KEY in attacker/.env (or your environment) and restart the app.",
            )
            return
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, "Catalog error", str(exc))
            return
        persona = self.persona_select.currentText()
        model = self.model_input.text().strip() or GROQ_DEFAULT_MODEL
        extra = ""
        self._set_plan_generating(True)
        self._planner_worker = PlannerWorker(
            api_key,
            model,
            catalog,
            persona,
            extra,
            test_json_only=False,
        )
        self._planner_worker.finished_ok.connect(self._on_planner_ok)
        self._planner_worker.finished_err.connect(self._on_planner_err)
        self._planner_worker.finished.connect(self._on_planner_finished)
        self._planner_worker.start()

    def generate_locustfile(self):
        if self._planner_worker is not None or self._locustfile_worker is not None:
            return
        if not os.path.exists(CATALOG_PATH):
            QMessageBox.warning(
                self,
                "No catalog",
                "Target catalog not loaded yet. Edit Target Host (valid URL) and wait a moment, or ensure the demo is running.",
            )
            return
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            QMessageBox.warning(
                self,
                "API key needed",
                "Set GROQ_API_KEY in attacker/.env (or your environment) and restart the app.",
            )
            return
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, "Catalog error", str(exc))
            return
        bot_type = persona_to_bot_type(self.persona_select.currentText())
        model = self.model_input.text().strip() or GROQ_DEFAULT_MODEL
        extra = ""
        self._set_plan_generating(True)
        self.status_label.setText("Generating locustfile.py (Groq)…")
        self._locustfile_worker = LocustfileGenWorker(api_key, model, catalog, bot_type, extra)
        self._locustfile_worker.finished_ok.connect(self._on_locustfile_ok)
        self._locustfile_worker.finished_err.connect(self._on_locustfile_err)
        self._locustfile_worker.finished.connect(self._on_locustfile_finished)
        self._locustfile_worker.start()

    def _on_locustfile_ok(self, code: str):
        self.plan_box.setPlainText(code)
        self.status_label.setText(f"Wrote {LOCUSTFILE}")
        QMessageBox.information(
            self,
            "locustfile.py",
            f"Saved generated code to:\n{LOCUSTFILE}\n\nReview the editor, then run Start Bots (uses this file).",
        )

    def _on_locustfile_err(self, message: str, bad_code: str):
        if bad_code.strip():
            self.plan_box.setPlainText(bad_code)
        QMessageBox.critical(self, "locustfile generation failed", message)

    def _on_locustfile_finished(self):
        self._locustfile_worker = None
        self.generate_btn.setEnabled(True)
        self.gen_locustfile_btn.setEnabled(True)
        self._tick()

    def approve_plan(self):
        try:
            plan = json.loads(self.plan_box.toPlainText().strip())
        except Exception as exc:
            QMessageBox.critical(self, "Invalid Plan JSON", str(exc))
            return
        allowed = {
            "browse_home",
            "view_dashboard",
            "view_concert",
            "buy_ticket",
            "signup_only",
            "invalid_login",
        }
        if os.path.exists(CATALOG_PATH):
            try:
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    cat = json.load(f)
                allow_cat = cat.get("actions_allowlist")
                if isinstance(allow_cat, list) and allow_cat:
                    allowed = set(allow_cat)
            except Exception:
                pass
        for action in plan.get("actions", []):
            if action.get("name") not in allowed:
                QMessageBox.critical(self, "Plan Rejected", f"Unsupported action: {action.get('name')}")
                return
        if "spawn_rate" not in plan or "duration" not in plan:
            QMessageBox.critical(self, "Plan Rejected", "Plan must include spawn_rate and duration.")
            return
        os.makedirs(PLANS_DIR, exist_ok=True)
        archive_name = next_plan_name(plan.get("persona", "bot"))
        archive_path = os.path.join(PLANS_DIR, archive_name)
        with open(PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)
        QMessageBox.information(self, "Plan Approved", f"Saved approved plan.\nLatest: {PLAN_PATH}\nArchived: {archive_path}")

    def _locust_command(self):
        locust_exec = LOCUST_BIN if os.path.exists(LOCUST_BIN) else "locust"
        try:
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception:
            plan = {}
        spawn_rate = str(plan.get("spawn_rate", 10))
        duration = str(plan.get("duration", "2m"))
        return [
            locust_exec,
            "-f",
            LOCUSTFILE,
            "--host",
            self.host_input.text().strip(),
            "--headless",
            "-u",
            self.users_input.text().strip(),
            "-r",
            spawn_rate,
            "-t",
            duration,
            "--only-summary",
        ]

    def start_bots(self):
        if self.process and self.process.poll() is None:
            QMessageBox.information(self, "Locust", "Locust is already running.")
            return
        if not os.path.exists(PLAN_PATH):
            QMessageBox.critical(self, "Locust", "No approved_plan.json found. Generate + Approve plan first.")
            return
        if not os.path.exists(LOCUSTFILE):
            QMessageBox.critical(self, "Locust", f"Missing file: {LOCUSTFILE}")
            return
        try:
            self.log_file = open(LOG_PATH, "w", encoding="utf-8")
            self.process = subprocess.Popen(
                self._locust_command(),
                cwd=BASE_DIR,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
            )
            self.status_label.setText(f"Running (pid {self.process.pid})")
            self.refresh_log()
        except Exception as exc:
            QMessageBox.critical(self, "Locust Start Failed", str(exc))

    def stop_bots(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.status_label.setText("Stopping...")
        else:
            self.status_label.setText("Stopped")

    def refresh_log(self):
        if not os.path.exists(LOG_PATH):
            text = "No log output yet."
        else:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            text = "".join(lines[-120:]) if lines else "No log output yet."
        self.log_box.setPlainText(text)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _tick(self):
        if self._planner_worker is not None:
            self.status_label.setText("Generating plan…")
        elif self._locustfile_worker is not None:
            self.status_label.setText("Generating locustfile.py (Groq)…")
        elif self.process and self.process.poll() is None:
            self.status_label.setText(f"Running (pid {self.process.pid})")
        elif self.process:
            self.status_label.setText(f"Stopped (exit {self.process.returncode})")
        else:
            self.status_label.setText("Stopped")
        self.refresh_log()

    def show_approved_plans_view(self):
        self.refresh_approved_plans_list()
        self.stack.setCurrentIndex(1)

    def refresh_approved_plans_list(self):
        self.plans_list.clear()
        os.makedirs(PLANS_DIR, exist_ok=True)
        files = []
        for name in os.listdir(PLANS_DIR):
            if name.endswith(".json"):
                files.append(os.path.join(PLANS_DIR, name))
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for path in files:
            item = QListWidgetItem(os.path.basename(path))
            item.setIcon(self.icon_provider.icon(QFileInfo(path)))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.plans_list.addItem(item)
        if not files:
            empty = QListWidgetItem("No approved plans yet")
            empty.setFlags(Qt.NoItemFlags)
            self.plans_list.addItem(empty)

    def open_selected_plan(self, item=None):
        selected = item or self.plans_list.currentItem()
        if not selected:
            QMessageBox.information(self, "No selection", "Select a plan file first.")
            return
        path = selected.data(Qt.UserRole)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = LocustControlWindow()
    window.show()
    sys.exit(app.exec())
