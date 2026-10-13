"""Basic safety-net tests.  Run from the project folder with:   python -m unittest discover -s tests -v

They use a throw-away SQLite database in a temp folder, never your real hse.db.
"""
import os
import sys
import tempfile
import unittest

# Must be set BEFORE the app (and config.py) is imported.
_TMP = tempfile.mkdtemp(prefix="hse_test_")
os.environ["HSE_DATA_DIR"] = _TMP
os.environ.pop("DATABASE_URL", None)
os.environ.pop("RENDER", None)
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ.pop("FIELD_ENCRYPTION_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db                       # noqa: E402
from app.models import User, LoginAttempt            # noqa: E402

EMAIL = "tester@example.com"
PASSWORD = "Correct-Horse-1"


def make_app():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    return app


def login(client, password, email=EMAIL):
    return client.post("/auth/login", data={"email": email, "password": password})


class BaseCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = make_app()
        with cls.app.app_context():
            if not User.query.filter_by(email=EMAIL).first():
                u = User(name="Tester", email=EMAIL, is_admin=True)
                u.set_password(PASSWORD)
                db.session.add(u)
                db.session.commit()

    def setUp(self):
        with self.app.app_context():
            LoginAttempt.query.delete()
            db.session.commit()
        self.client = self.app.test_client()


class LoginTests(BaseCase):
    def test_login_page_loads(self):
        self.assertEqual(self.client.get("/auth/login").status_code, 200)

    def test_private_page_redirects_to_login(self):
        r = self.client.get("/auth/profile")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/auth/login", r.headers["Location"])

    def test_good_login_then_private_page_works(self):
        r = login(self.client, PASSWORD)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/auth/profile").status_code, 200)

    def test_wrong_password_is_rejected(self):
        r = login(self.client, "wrong")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/auth/profile").status_code, 302)

    def test_external_next_url_is_ignored(self):
        r = self.client.post("/auth/login?next=https://evil.example/",
                             data={"email": EMAIL, "password": PASSWORD})
        self.assertNotIn("evil.example", r.headers["Location"])


class LockoutTests(BaseCase):
    def test_five_failures_lock_even_the_right_password(self):
        for _ in range(5):
            login(self.client, "wrong")
        self.assertEqual(login(self.client, PASSWORD).status_code, 429)

    def test_failures_are_stored_in_the_database(self):
        """The old counter lived in each worker's memory (so 2 gunicorn workers = double the
        attempts, and a restart reset it). Now every failure is a database row, which all
        workers and restarts share."""
        for _ in range(5):
            login(self.client, "wrong")
        with self.app.app_context():
            self.assertEqual(LoginAttempt.query.filter_by(email=EMAIL).count(), 5)
        other_worker = make_app().test_client()          # a fresh app = like another worker
        self.assertEqual(login(other_worker, PASSWORD).status_code, 429)

    def test_success_clears_the_counter(self):
        for _ in range(3):
            login(self.client, "wrong")
        self.assertEqual(login(self.client, PASSWORD).status_code, 302)
        with self.app.app_context():
            self.assertEqual(LoginAttempt.query.count(), 0)


class EncryptionTests(BaseCase):
    def test_roundtrip(self):
        from app.security import encrypt_text, decrypt_text
        with self.app.app_context():
            token = encrypt_text("portal-pass-123")
            self.assertTrue(token.startswith("enc::"))
            self.assertEqual(decrypt_text(token), "portal-pass-123")

    def test_adding_a_separate_key_keeps_old_data_readable(self):
        from cryptography.fernet import Fernet
        from app.security import encrypt_text, decrypt_text
        with self.app.app_context():
            old_token = encrypt_text("old-secret")                 # made with the SECRET_KEY-derived key
            self.app.config["FIELD_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
            try:
                self.assertEqual(decrypt_text(old_token), "old-secret")      # still readable
                new_token = encrypt_text("new-secret")                       # now uses the new key
                self.assertEqual(decrypt_text(new_token), "new-secret")
            finally:
                self.app.config["FIELD_ENCRYPTION_KEY"] = None

    def test_bad_key_gives_a_clear_error(self):
        from app.security import encrypt_text
        with self.app.app_context():
            self.app.config["FIELD_ENCRYPTION_KEY"] = "not-a-valid-key"
            try:
                with self.assertRaises(RuntimeError):
                    encrypt_text("x")
            finally:
                self.app.config["FIELD_ENCRYPTION_KEY"] = None


if __name__ == "__main__":
    unittest.main()
