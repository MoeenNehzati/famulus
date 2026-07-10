from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "_rtx"
sys.path.insert(0, str(SCRIPT_DIR))
import _oauth_bootstrap as setup_oauth  # noqa: E402


class SetupOauthTests(unittest.TestCase):
    def test_load_client_credentials_from_google_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "cid",
                            "client_secret": "secret",
                        }
                    }
                ),
                encoding="utf-8",
            )
            client_id, client_secret = setup_oauth.load_client_credentials(
                from_json=path,
                client_id=None,
                client_secret=None,
            )
        self.assertEqual(client_id, "cid")
        self.assertEqual(client_secret, "secret")

    def test_build_auth_url_contains_drive_scope(self) -> None:
        url = setup_oauth.build_auth_url(
            client_id="cid",
            redirect_uri="http://localhost:8765",
            state="abc",
        )
        self.assertIn("scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive", url)
        self.assertIn("client_id=cid", url)
        self.assertIn("state=abc", url)


if __name__ == "__main__":
    unittest.main()
