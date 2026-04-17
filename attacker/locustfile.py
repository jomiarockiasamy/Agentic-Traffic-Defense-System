import random
import uuid

from locust import HttpUser, between, task


class WebsiteUser(HttpUser):
    # Fast loop so inventory fills quickly in demo.
    wait_time = between(0.05, 0.2)

    def on_start(self):
        self.email = f"u_{uuid.uuid4().hex[:8]}@demo.test"
        self.password = "pass123"
        self.client.post(
            "/signup",
            data={"name": "Demo User", "email": self.email, "password": self.password},
            allow_redirects=True,
            name="POST /signup",
        )

    @task(1)
    def browse(self):
        self.client.get("/", name="GET /")
        self.client.get("/dashboard", name="GET /dashboard")

    @task(1)
    def view_concert(self):
        cid = random.choice(["concert1", "concert2", "concert3"])
        self.client.get(f"/{cid}", name="GET /concert")

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
            allow_redirects=True,
            name="POST /buy/<concert>",
        )


class NoisyAuthUser(HttpUser):
    # Keep a little auth pressure, but less than buyers.
    wait_time = between(0.1, 0.4)

    @task
    def auth_spam_pattern(self):
        self.client.get("/auth", name="GET /auth")
        self.client.post(
            "/login",
            data={"email": "fake@demo.test", "password": "badpass"},
            allow_redirects=True,
            name="POST /login (invalid)",
        )
