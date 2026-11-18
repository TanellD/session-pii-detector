"""Только вымышленные имена и ответы VK; сеть не используется."""

from pathlib import Path
import sys
import unittest
import os
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from collect_vk_name_pairs import normalize_items, api, ApiFailure, settings


class VkNamePairTests(unittest.TestCase):
    def test_service_and_user_tokens_are_separate(self):
        with patch.dict(os.environ, {"VK_ACCESS_TOKEN": "synthetic-user", "VK_SERVICE_TOKEN": "synthetic-service"}, clear=True), \
             patch("collect_vk_name_pairs.Path.exists", return_value=False):
            self.assertEqual(settings()[0], "synthetic-user")
            self.assertEqual(settings("VK_SERVICE_TOKEN")[0], "synthetic-service")
        with self.assertRaises(ValueError):
            settings("OTHER_SECRET")

    def test_only_supported_open_accounts_and_whitelisted_fields_saved(self):
        item = {"id": 1, "first_name": "Тестимир", "last_name": "Вымышлов", "sex": 2,
                "contacts": "synthetic-unused-field", "nickname": "synthetic-unused-field"}
        records, counts = normalize_items([item, item, {**item, "id": 2, "is_closed": True},
                                          {**item, "id": 3, "first_name": "Тестимир Примерович"}])
        self.assertEqual(len(records), 1)
        self.assertEqual(set(records[0]), {"account_id", "name", "surname", "sex"})
        self.assertEqual(counts["duplicate_accounts"], 1)
        self.assertEqual(counts["unsupported_written_name"], 1)

    def test_token_sent_only_in_post_body_and_error_not_exported(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return b'{"error":{"error_code":5,"error_msg":"access_token has expired.","request_params":["synthetic-secret"]}}'
        with patch("collect_vk_name_pairs.build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse()
            with self.assertRaises(ApiFailure) as failure:
                api("users.search", {"count": 1}, "synthetic-secret", "5.199")
            request = opener.return_value.open.call_args.args[0]
        self.assertNotIn("synthetic-secret", request.full_url)
        self.assertIn(b"access_token=synthetic-secret", request.data)
        self.assertNotIn("synthetic-secret", str(failure.exception))
        self.assertEqual(failure.exception.code, 5)
        self.assertEqual(failure.exception.reason, "access_token_expired")


if __name__ == "__main__":
    unittest.main()
