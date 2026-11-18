"""Проверка подписи и ограниченного пилота ОК на синтетических ответах."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from probe_ok_name_access import probe, signed_parameters, OkFailure

CONFIG = {"OK_APPLICATION_KEY": "test-public", "OK_APPLICATION_SECRET_KEY": "test-secret",
          "OK_ACCESS_TOKEN": "test-token"}


class OkAccessProbeTests(unittest.TestCase):
    def test_missing_configuration_does_not_call_api(self):
        with patch("probe_ok_name_access.api") as request:
            result = probe({}, "123")
        request.assert_not_called()
        self.assertFalse(result["network_used"])

    def test_hidden_members_do_not_trigger_profile_download(self):
        with patch("probe_ok_name_access.api", return_value={"membersHidden": True, "members": []}) as request:
            result = probe(CONFIG, "123")
        self.assertEqual(result["status"], "members_hidden")
        self.assertEqual(request.call_count, 1)

    def test_probe_bounds_requests_deduplicates_ids_and_exports_no_names(self):
        page = {"members": [{"userId": "1"}, {"userId": "1"}, {"userId": "2"}], "anchor": "synthetic-cursor", "has_more": True}
        users = [{"uid": "1", "first_name": "SyntheticFirst", "last_name": "SyntheticLast"},
                 {"uid": "2", "private": True, "first_name": "SyntheticFirst", "last_name": "SyntheticLast"}]
        with patch("probe_ok_name_access.api", side_effect=[page, users]) as request, patch("probe_ok_name_access.time.sleep"):
            result = probe(CONFIG, "123")
        self.assertEqual(request.call_args_list[0].args[1]["count"], "10")
        self.assertEqual(request.call_args_list[1].args[1]["uids"], "1,2")
        self.assertEqual(result["quality"]["public_written_name_and_surname"], 1)
        self.assertNotIn("Synthetic", str(result))
        self.assertNotIn("synthetic-cursor", str(result))
        self.assertFalse(result["million_record_access_confirmed"])

    def test_error_is_sanitized(self):
        error = OkFailure(8, 1000)
        self.assertEqual(error.code, 8)
        self.assertEqual(error.wait_millis, 1000)
        self.assertEqual(str(error), "OK API request failed")

    def test_authentication_parameters_cannot_be_overridden(self):
        with self.assertRaises(ValueError):
            signed_parameters("users.getInfo", {"access_token": "other-synthetic-token"}, CONFIG)

    def test_signature_has_fixed_vector_and_does_not_transmit_secret(self):
        result = signed_parameters("group.getMembers", {"uid": "123"}, CONFIG)
        self.assertNotIn("test-secret", str(result))
        self.assertEqual(result["access_token"], "test-token")
        self.assertEqual(result["sig"], "44f41e32444ec35d616b8bacc7d98778")


if __name__ == "__main__":
    unittest.main()
