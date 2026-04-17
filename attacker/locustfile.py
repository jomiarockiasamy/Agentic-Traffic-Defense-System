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
import uuid

from locust import HttpUser, between, task


def _random_public_ipv4() -> str:
    """Fake client IP for X-Forwarded-For (demo only; not routable realism)."""
    return ".".join(str(random.randint(1, 223)) for _ in range(4))


def _xff_headers() -> dict[str, str]:
    return {"X-Forwarded-For": _random_public_ipv4()}


class WebsiteUser(HttpUser):
    # Fast loop so inventory fills quickly in demo.
    wait_time = between(0.05, 0.2)

    def on_start(self):
        self.email = f"u_{uuid.uuid4().hex[:8]}@demo.test"
        self.password = "pass123"
        self.client.post(
            "/signup",
            data={"name": "Demo User", "email": self.email, "password": self.password},
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
        # Weighted choice pushes more demand into Concert 1.
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
    # Keep a little auth pressure, but less than buyers.
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
