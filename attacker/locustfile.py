"""
Load test for the demo Flask app.

Each virtual user sends a random X-Forwarded-For so the defender sees many
simulated client IPs (Locust itself still runs from one machine).

Attack preset (judges / spike demo):
  locust -f locustfile.py --host=http://127.0.0.1:5001 \\
    --users 200 --spawn-rate 15 --run-time 120s --headless

See ATTACK.md in this folder.
"""
import random

from locust import HttpUser, between, task

# List of names for signup (from attacker-code branch)
SIGNUP_NAMES = (
    "John Doe", "Jane Smith", "Alice Johnson", "Bob Brown", "Eve Davis",
    "Mike Miller", "Emma Taylor", "David Lee", "Sophia Hall", "Oliver Martin",
    "Ava White", "William Harris", "Isabella Thompson", "James Wilson", "Charlotte Lewis",
    "George Russell", "Amelia Walker", "Benjamin Young", "Harper Jenkins", "Alexander Brooks",
    "Evelyn Sanders", "Daniel Patel", "Abigail Kim", "Jackson Lee", "Emily Chen",
    "Michael Brown", "Sarah Taylor", "William White", "Olivia Martin", "James Harris",
    "Ava Thompson", "George Wilson", "Isabella Lewis", "Benjamin Hall", "Charlotte Walker",
    "Alexander Jenkins", "Evelyn Brooks", "Daniel Sanders", "Abigail Patel", "Jackson Kim",
    "Emily Lee", "Michael Chen", "Sarah Brown", "William Taylor", "Olivia White",
)


def make_email_from_full_name(full_name: str) -> str:
    """Pick one of three local-part styles at random; numeric suffix keeps emails unique under load."""
    parts = full_name.strip().split()
    if len(parts) < 2:
        return f"user_{random.randint(100000, 999999)}@example.com"
    first, last = parts[0], parts[-1]
    fl, ll = first.lower(), last.lower()
    li = last[0].lower()

    kind = random.randrange(3)
    if kind == 0:
        local = f"{fl}_{ll}"
    elif kind == 1:
        local = f"{li}_{fl}"
    else:
        local = f"{fl}_{random.randint(100, 999):03d}"

    return f"{local}_{random.randint(10000, 99999)}@example.com"


def _random_public_ipv4() -> str:
    """Fake client IP for X-Forwarded-For (demo only; not routable realism)."""
    return ".".join(str(random.randint(1, 223)) for _ in range(4))


def _xff_headers() -> dict[str, str]:
    return {"X-Forwarded-For": _random_public_ipv4()}


class WebsiteUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self):
        full_name = random.choice(SIGNUP_NAMES)
        self.email = make_email_from_full_name(full_name)
        self.password = "pass123"
        self.client.post(
            "/signup",
            data={"name": full_name, "email": self.email, "password": self.password},
            headers=_xff_headers(),
            allow_redirects=True,
            name="POST /signup",
        )

    @task(1)
    def browse(self):
        self.client.get("/", headers=_xff_headers(), name="GET /")
        self.client.get("/dashboard", headers=_xff_headers(), name="GET /dashboard")

    @task(1)
    def view_concert(self):
        cid = random.choice(["concert1", "concert2", "concert3"])
        self.client.get(f"/{cid}", headers=_xff_headers(), name="GET /concert")

    @task(8)
    def buy_ticket(self):
        cid = random.choices(
            ["concert1", "concert2", "concert3"],
            weights=[0.6, 0.25, 0.15],
            k=1,
        )[0]
        qty = random.choices([1, 2, 3], weights=[0.55, 0.3, 0.15], k=1)[0]
        self.client.post(
            f"/buy/{cid}",
            data={"quantity": str(qty)},
            headers=_xff_headers(),
            allow_redirects=True,
            name="POST /buy/<concert>",
        )


class NoisyAuthUser(HttpUser):
    wait_time = between(0.1, 0.4)

    @task
    def auth_spam_pattern(self):
        self.client.get("/auth", headers=_xff_headers(), name="GET /auth")
        self.client.post(
            "/login",
            data={"email": "fake@demo.test", "password": "badpass"},
            headers=_xff_headers(),
            allow_redirects=True,
            name="POST /login (invalid)",
        )
