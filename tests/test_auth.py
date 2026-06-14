"""Tests for optional local API token authentication (autoprompt_runner.auth)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import auth, settings as settings_mod  # noqa: E402

_AUTH_ENV = ("AUTOPROMPT_AUTH_ENABLED", "AUTOPROMPT_API_TOKEN", "AUTOPROMPT_ALLOW_UNAUTHENTICATED_HEALTH")


def _clear_auth_env():
    for name in _AUTH_ENV:
        os.environ.pop(name, None)


class AuthHelperTests(unittest.TestCase):
    def test_generate_api_token_is_nonempty_and_unique(self):
        a = auth.generate_api_token()
        b = auth.generate_api_token()
        self.assertGreaterEqual(len(a), 32)
        self.assertNotEqual(a, b)
        self.assertEqual(a, a.strip())

    def test_redact_token(self):
        self.assertEqual(auth.redact_token("a-secret"), "(set, redacted)")
        self.assertEqual(auth.redact_token(""), "(unset)")
        self.assertEqual(auth.redact_token(None), "(unset)")

    def _settings(self, enabled, token):
        s = settings_mod.build_default_settings()
        s.auth.enabled = enabled
        s.auth.api_token = token
        return s

    def test_is_auth_enabled(self):
        self.assertFalse(auth.is_auth_enabled(self._settings(False, "")))
        self.assertTrue(auth.is_auth_enabled(self._settings(True, "x")))

    def test_validate_bearer_token(self):
        s = self._settings(True, "the-token")
        self.assertTrue(auth.validate_bearer_token("Bearer the-token", s))
        self.assertFalse(auth.validate_bearer_token("Bearer wrong", s))
        self.assertFalse(auth.validate_bearer_token("the-token", s))  # no scheme
        self.assertFalse(auth.validate_bearer_token(None, s))
        self.assertFalse(auth.validate_bearer_token("Bearer ", s))

    def test_require_api_auth_noop_when_disabled(self):
        # Disabled -> never raises, even without a token.
        auth.require_api_auth(None, self._settings(False, ""))

    def test_require_api_auth_raises_when_enabled_and_invalid(self):
        with self.assertRaises(auth.AuthError):
            auth.require_api_auth(None, self._settings(True, "tok"))
        with self.assertRaises(auth.AuthError):
            auth.require_api_auth("Bearer nope", self._settings(True, "tok"))
        # Valid token does not raise.
        auth.require_api_auth("Bearer tok", self._settings(True, "tok"))


class AuthValidationTests(unittest.TestCase):
    def _validate(self, enabled, token):
        s = settings_mod.build_default_settings()
        s.auth.enabled = enabled
        s.auth.api_token = token
        settings_mod.validate_settings(s)

    def test_validate_fails_when_enabled_without_token(self):
        with self.assertRaises(settings_mod.SettingsError):
            self._validate(True, "")

    def test_validate_ok_when_enabled_with_token(self):
        self._validate(True, "a-token")  # no raise

    def test_validate_ok_when_disabled_without_token(self):
        self._validate(False, "")  # no raise


class AuthEnvTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()

    def tearDown(self):
        _clear_auth_env()

    def test_env_overrides_enable_auth(self):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = "env-token"
        s = settings_mod.load_settings()
        self.assertTrue(s.auth.enabled)
        self.assertEqual(s.auth.api_token, "env-token")
        self.assertTrue(s.auth.allow_unauthenticated_health)  # default

    def test_default_is_disabled(self):
        s = settings_mod.load_settings()
        self.assertFalse(s.auth.enabled)


try:
    from fastapi.testclient import TestClient

    from autoprompt_runner import storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class AuthApiTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        _clear_auth_env()
        self._tmp.cleanup()

    def _enable(self, token="secret-token", allow_health=True):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = token
        os.environ["AUTOPROMPT_ALLOW_UNAUTHENTICATED_HEALTH"] = "true" if allow_health else "false"

    def test_disabled_allows_requests(self):
        self.assertEqual(self.client.get("/projects").status_code, 200)

    def test_enabled_rejects_missing_token(self):
        self._enable()
        self.assertEqual(self.client.get("/projects").status_code, 401)

    def test_enabled_rejects_invalid_token(self):
        self._enable()
        resp = self.client.get("/projects", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_enabled_accepts_valid_token(self):
        self._enable(token="secret-token")
        resp = self.client.get("/projects", headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(resp.status_code, 200)

    def test_health_public_when_allowed(self):
        self._enable(allow_health=True)
        self.assertEqual(self.client.get("/health").status_code, 200)  # no token needed

    def test_health_protected_when_not_allowed(self):
        self._enable(token="secret-token", allow_health=False)
        self.assertEqual(self.client.get("/health").status_code, 401)
        ok = self.client.get("/health", headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(ok.status_code, 200)

    def test_token_not_in_health_response(self):
        self._enable(token="secret-token")
        body = self.client.get("/health").json()
        self.assertTrue(body["config"]["auth_enabled"])
        self.assertNotIn("secret-token", self.client.get("/health").text)


if __name__ == "__main__":
    unittest.main()
