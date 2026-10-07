import os
import unittest
from unittest.mock import MagicMock, patch

from snclient import AuthError, RateLimitError, ServiceNowClient, ServiceNowError, Settings

SETTINGS = Settings("https://example.service-now.com", "cid", "csecret", "svc.user", "not-a-real-password", max_retries=2)


def resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.headers = headers or {}
    return r


def make_client(api_responses, token_responses=None):
    session = MagicMock()
    session.post.side_effect = token_responses or [resp(200, {"access_token": "t1", "expires_in": 1800})] * 5
    session.request.side_effect = api_responses
    sleeps = []
    client = ServiceNowClient(SETTINGS, session=session, sleep=sleeps.append, clock=lambda: 0.0)
    return client, session, sleeps


class ClientTests(unittest.TestCase):
    def test_paginates_until_short_page(self):
        pages = [resp(200, {"result": [{"sys_id": "a"}, {"sys_id": "b"}]}), resp(200, {"result": [{"sys_id": "c"}]})]
        client, session, _ = make_client(pages)
        rows = list(client.get_records("incident", query="active=true", page_size=2))
        assert [r["sys_id"] for r in rows] == ["a", "b", "c"]
        offsets = [c.kwargs["params"]["sysparm_offset"] for c in session.request.call_args_list]
        assert offsets == [0, 2]
        assert session.request.call_args_list[0].kwargs["params"]["sysparm_query"] == "active=true^ORDERBYsys_id"


    def test_limit_stops_early(self):
        client, session, _ = make_client([resp(200, {"result": [{"sys_id": "a"}, {"sys_id": "b"}]})])
        assert len(list(client.get_records("incident", page_size=2, limit=1))) == 1
        assert session.request.call_count == 1


    def test_token_is_cached_between_calls(self):
        client, session, _ = make_client([resp(200, {"result": []}), resp(200, {"result": []})])
        list(client.get_records("incident"))
        list(client.get_records("incident"))
        assert session.post.call_count == 1


    def test_refreshes_token_once_on_401(self):
        tokens = [resp(200, {"access_token": "old"}), resp(200, {"access_token": "new"})]
        client, session, _ = make_client([resp(401), resp(200, {"result": []})], tokens)
        list(client.get_records("incident"))
        assert session.post.call_count == 2
        assert session.request.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new"


    def test_second_401_raises_auth_error(self):
        client, _, _ = make_client([resp(401), resp(401)])
        with self.assertRaises(AuthError):
            list(client.get_records("incident"))


    def test_retries_on_503_with_backoff(self):
        client, _, sleeps = make_client([resp(503), resp(503), resp(200, {"result": []})])
        list(client.get_records("incident"))
        assert sleeps == [1.0, 2.0]


    def test_honors_retry_after_header(self):
        client, _, sleeps = make_client([resp(429, headers={"Retry-After": "7"}), resp(200, {"result": []})])
        list(client.get_records("incident"))
        assert sleeps == [7.0]


    def test_rate_limit_error_after_retries_exhausted(self):
        client, _, sleeps = make_client([resp(429)] * 3)
        with self.assertRaises(RateLimitError):
            list(client.get_records("incident"))
        assert len(sleeps) == 2


    def test_client_error_carries_status_and_message(self):
        client, _, _ = make_client([resp(403, {"error": {"message": "ACL denied"}})])
        with self.assertRaises(ServiceNowError) as err:
            client.create_record("incident", {"short_description": "x"})
        assert err.exception.status == 403 and "ACL denied" in str(err.exception)


    def test_failed_token_request_does_not_leak_body(self):
        client, _, _ = make_client([], [resp(400, {"error_description": "csecret"})])
        with self.assertRaises(AuthError) as err:
            list(client.get_records("incident"))
        assert "csecret" not in str(err.exception)


    def test_upsert_creates_when_missing(self):
        client, session, _ = make_client([resp(200, {"result": []}), resp(201, {"result": {"sys_id": "n1"}})])
        action, record = client.upsert("cmdb_ci_server", "serial_number", {"serial_number": "SN-1", "name": "srv"})
        assert (action, record["sys_id"]) == ("created", "n1")
        assert session.request.call_args_list[1].args[0] == "POST"


    def test_upsert_updates_when_found(self):
        client, session, _ = make_client([resp(200, {"result": [{"sys_id": "e1"}]}), resp(200, {"result": {"sys_id": "e1"}})])
        action, _ = client.upsert("cmdb_ci_server", "serial_number", {"serial_number": "SN-1"})
        assert action == "updated"
        assert session.request.call_args_list[1].args[:2] == ("PATCH", "https://example.service-now.com/api/now/table/cmdb_ci_server/e1")


    def test_upsert_refuses_ambiguous_match(self):
        client, _, _ = make_client([resp(200, {"result": [{"sys_id": "e1"}, {"sys_id": "e2"}]})])
        with self.assertRaisesRegex(ServiceNowError, "refusing to update"):
            client.upsert("cmdb_ci_server", "serial_number", {"serial_number": "SN-1"})


ENV = {"SN_INSTANCE_URL": "https://x.service-now.com", "SN_CLIENT_ID": "a", "SN_CLIENT_SECRET": "b", "SN_USERNAME": "c", "SN_PASSWORD": "d"}


class SettingsTests(unittest.TestCase):
    def test_reads_environment(self):
        with patch.dict(os.environ, ENV, clear=True):
            self.assertEqual(Settings.from_env().instance_url, "https://x.service-now.com")

    def test_requires_https(self):
        with patch.dict(os.environ, {**ENV, "SN_INSTANCE_URL": "http://x"}, clear=True):
            with self.assertRaisesRegex(ValueError, "https"):
                Settings.from_env()

    def test_lists_missing_variables(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SN_CLIENT_SECRET"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
