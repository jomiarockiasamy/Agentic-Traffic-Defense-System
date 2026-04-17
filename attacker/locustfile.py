import random

from locust import HttpUser, task, between

# List of names for signup
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
        # firstname_lastname
        local = f"{fl}_{ll}"
    elif kind == 1:
        # lastInitial_firstname
        local = f"{li}_{fl}"
    else:
        # firstname + 3 digits
        local = f"{fl}_{random.randint(100, 999):03d}"

    # tie-break so concurrent signups never reuse the same address
    return f"{local}_{random.randint(10000, 99999)}@example.com"


class DemoUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task(1)
    def signup(self):
        """Create a new account"""
        name = random.choice(SIGNUP_NAMES)
        email = make_email_from_full_name(name)
        password = "password123"
        self.client.post("/signup", data={"name": name, "email": email, "password": password})

# ASSUMPTIONS:
# - This script assumes that the demo site is running and accessible.
# - It also assumes that the site's signup endpoint is correctly implemented and accepts the required form fields (name, email, password).
# - Per-user source IP rotation is not achievable per virtual user in plain Locust; this would require OS-level socket binding or a more advanced load testing setup.
