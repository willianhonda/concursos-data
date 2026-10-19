import base64
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import push_novos as pn  # noqa: E402

ACCOUNT = {"client_email": "fcm@concursos-publicos.iam.gserviceaccount.com", "private_key": "PEM",
           "private_key_id": "abc123", "token_uri": "https://oauth2.googleapis.com/token"}


def unb64(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class NewIdsTests(unittest.TestCase):
    def test_only_ids_missing_from_the_previous_feed_in_feed_order(self):
        previous = [{"id": "a"}, {"id": "b"}]
        current = [{"id": "d"}, {"id": "a"}, {"id": "c"}, {"id": "d"}, {"title": "sem id"}, "lixo"]
        self.assertEqual(pn.new_ids(previous, current), ["d", "c"])

    def test_removed_items_are_not_news(self):
        self.assertEqual(pn.new_ids([{"id": "a"}, {"id": "b"}], [{"id": "a"}]), [])

    def test_unreadable_previous_counts_as_empty(self):
        self.assertEqual(pn.new_ids({"erro": 1}, [{"id": "a"}]), ["a"])


class MessageTests(unittest.TestCase):
    def test_silent_data_only_message(self):
        message = pn.build_message(["3f1c9a0b2d4e", "c720468890b1"])
        self.assertEqual(message, {"message": {
            "topic": "novos-concursos",
            "data": {"ids": "3f1c9a0b2d4e,c720468890b1", "count": "2"},
            "apns": {
                "headers": {"apns-push-type": "background", "apns-priority": "5"},
                "payload": {"aps": {"content-available": 1}},
            },
        }})
        self.assertNotIn("notification", message["message"])

    def test_ids_are_capped_without_cutting_an_id(self):
        ids = [f"{n:012d}" for n in range(500)]
        message = pn.build_message(ids)
        text = message["message"]["data"]["ids"]
        self.assertLessEqual(len(text), pn.MAX_IDS_CHARS)
        self.assertTrue(all(len(part) == 12 for part in text.split(",")))
        self.assertEqual(text.split(",")[0], "000000000000")
        self.assertEqual(message["message"]["data"]["count"], "500")


class AuthTests(unittest.TestCase):
    def test_jwt_claims_for_the_bearer_flow(self):
        signed = []
        token = pn.build_jwt(ACCOUNT, 1_700_000_000, sign=lambda key, data: signed.append((key, data)) or b"sig")
        header, claims, signature = token.split(".")
        self.assertEqual(unb64(header), {"alg": "RS256", "typ": "JWT", "kid": "abc123"})
        self.assertEqual(unb64(claims), {
            "iss": ACCOUNT["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.googleapis.com/token", "iat": 1_700_000_000, "exp": 1_700_003_600,
        })
        self.assertEqual(signed, [("PEM", f"{header}.{claims}".encode())])
        self.assertEqual(signature, pn.b64url(b"sig"))

    def test_send_exchanges_the_jwt_and_posts_to_fcm_v1(self):
        calls = []

        def fake_post(url, body, headers):
            calls.append((url, body, headers))
            return {"access_token": "tok"} if "oauth2" in url else {"name": "projects/concursos-publicos/messages/1"}

        message = pn.build_message(["a"])
        result = pn.send_message(message, ACCOUNT, "concursos-publicos", now=0, sign=lambda k, d: b"s", send=fake_post)
        self.assertEqual(result["name"], "projects/concursos-publicos/messages/1")
        (token_url, token_body, _), (fcm_url, fcm_body, fcm_headers) = calls
        self.assertEqual(token_url, "https://oauth2.googleapis.com/token")
        self.assertIn(b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer", token_body)
        self.assertEqual(fcm_url, "https://fcm.googleapis.com/v1/projects/concursos-publicos/messages:send")
        self.assertEqual(fcm_headers["Authorization"], "Bearer tok")
        self.assertEqual(json.loads(fcm_body), message)

    @unittest.skipUnless(shutil.which("openssl"), "precisa do openssl")
    def test_openssl_signature_verifies_with_the_public_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, pub, sig, data = (Path(tmp) / n for n in ("k.pem", "p.pem", "s.bin", "d.txt"))
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key)],
                           check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)
            token = pn.build_jwt({**ACCOUNT, "private_key": key.read_text()}, 0)
            signing_input, signature = token.rsplit(".", 1)
            data.write_bytes(signing_input.encode())
            sig.write_bytes(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
            check = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig), str(data)],
                                   capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr)


class MainTests(unittest.TestCase):
    def run_main(self, previous, current, *extra, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            prev_path, cur_path = Path(tmp) / "anterior.json", Path(tmp) / "concursos.json"
            if previous is not None:
                prev_path.write_text(json.dumps(previous))
            cur_path.write_text(json.dumps(current))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = pn.main([str(cur_path), "--previous", str(prev_path), *extra], env=env or {})
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_prints_the_message(self):
        code, out, _ = self.run_main([{"id": "a"}], [{"id": "b"}, {"id": "a"}], "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["message"]["data"], {"ids": "b", "count": "1"})

    def test_missing_secret_skips_without_failing(self):
        code, out, err = self.run_main([{"id": "a"}], [{"id": "b"}])
        self.assertEqual((code, out), (0, ""))
        self.assertIn("FIREBASE_SERVICE_ACCOUNT não definido", err)

    def test_nothing_new_sends_nothing(self):
        code, _, err = self.run_main([{"id": "a"}], [{"id": "a"}], env={"FIREBASE_SERVICE_ACCOUNT": "{inválido"})
        self.assertEqual(code, 0)
        self.assertIn("nenhum concurso novo", err)

    def test_missing_previous_feed_sends_nothing(self):
        code, _, err = self.run_main(None, [{"id": "a"}], env={"FIREBASE_SERVICE_ACCOUNT": "{inválido"})
        self.assertEqual(code, 0)
        self.assertIn("nada a comparar", err)

    def test_incomplete_service_account_is_an_error(self):
        with self.assertRaises(ValueError):
            pn.load_account('{"client_email": "x"}')
        self.assertIsNone(pn.load_account("  "))


if __name__ == "__main__":
    unittest.main()
