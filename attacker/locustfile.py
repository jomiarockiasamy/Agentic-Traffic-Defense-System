import uuid
import random
from locust import HttpUser, task, between

# List of names to choose from for signup
SIGNUP_NAMES = (
    "John Doe", "Jane Doe", "Alice Smith", "Bob Johnson", "Eve Williams",
    "Mike Davis", "Emma Taylor", "David Lee", "Sophia Martin", "Oliver Brown",
    "Ava White", "William Harris", "Isabella Thompson", "James Wilson", "Charlotte Miller",
    "George Anderson", "Abigail Thomas", "Robert Jackson", "Emily Clark", "Richard Lewis",
    "Harper Scott", "Charles Robinson", "Amelia Walker", "Thomas Young", "Evelyn Allen",
    "Benjamin Sanders", "Lily Nelson", "Logan Hall", "Madison Mitchell", "Alexander Russell",
    "Victoria Jenkins", "Ethan Brooks", "Jessica Garcia", "Noah Sanchez", "Samantha Rodriguez",
)

class DemoUser(HttpUser):
    wait_time = between(1, 2)

    @task(1)
    def signup(self):
        # Choose a random name from the list
        name = random.choice(SIGNUP_NAMES)
        # Generate a unique email using uuid
        email = f"user-{uuid.uuid4().hex}@example.com"
        # Generate a random password
        password = str(uuid.uuid4())
        # POST /signup with the required form fields
        self.client.post("/signup", data={"name": name, "email": email, "password": password})

# ASSUMPTIONS:
# - This script assumes that the demo site is running and accessible.
# - It also assumes that the /signup endpoint is available and functional.
# - The script does not simulate credential attacks, fraud patterns, or attempts to bypass limits.
# - Per-user source IP rotation is not achievable per virtual user in plain Locust.