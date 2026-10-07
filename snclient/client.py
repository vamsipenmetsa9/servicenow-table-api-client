"""Small client for the ServiceNow Table API.

Covers the parts an integration usually gets wrong: token refresh, paging with a
stable sort, retry with backoff on 429/5xx, and errors that say what happened.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator

import requests

from .config import Settings

log = logging.getLogger("snclient")

RETRY_STATUS = {429, 500, 502, 503, 504}


class ServiceNowError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(ServiceNowError):
    pass


class RateLimitError(ServiceNowError):
    pass


class ServiceNowClient:
    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._token_expiry = 0.0

    # ---- auth -------------------------------------------------------------
    def _get_token(self) -> str:
        if self._token and self._clock() < self._token_expiry - 30:
            return self._token
        response = self.session.post(
            f"{self.settings.instance_url}/oauth_token.do",
            data={
                "grant_type": "password",
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "username": self.settings.username,
                "password": self.settings.password,
            },
            timeout=self.settings.timeout,
        )
        if response.status_code != 200:
            # Never log the response body here: it can echo credentials.
            raise AuthError(f"OAuth token request failed with HTTP {response.status_code}", response.status_code)
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expiry = self._clock() + float(payload.get("expires_in", 1800))
        return self._token

    # ---- transport --------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.settings.instance_url}{path}"
        refreshed = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {self._get_token()}", "Accept": "application/json"}
            response = self.session.request(method, url, headers=headers, timeout=self.settings.timeout, **kwargs)
            status = response.status_code
            if status == 401 and not refreshed:
                log.info("401 from %s, refreshing token once", path)
                self._token = None
                refreshed = True
                continue
            if status in RETRY_STATUS and attempt < self.settings.max_retries:
                delay = self._retry_delay(response, attempt)
                log.warning("HTTP %s from %s, retry %s in %.1fs", status, path, attempt + 1, delay)
                self._sleep(delay)
                attempt += 1
                continue
            if status == 401:
                raise AuthError("Unauthorized after token refresh", status)
            if status == 429:
                raise RateLimitError("Rate limited and retries exhausted", status)
            if status >= 400:
                raise ServiceNowError(f"{method} {path} failed with HTTP {status}: {self._error_text(response)}", status)
            return response

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 60.0)
        return min(2.0**attempt, 30.0)

    @staticmethod
    def _error_text(response: requests.Response) -> str:
        try:
            return str(response.json().get("error", {}).get("message", ""))[:200]
        except ValueError:
            return ""

    # ---- table api --------------------------------------------------------
    def get_records(
        self, table: str, query: str = "", fields: list[str] | None = None, page_size: int = 500, limit: int | None = None
    ) -> Iterator[dict]:
        """Yield records page by page. ORDERBYsys_id keeps pages stable while data changes."""
        if "ORDERBY" not in query:
            query = f"{query}^ORDERBYsys_id" if query else "ORDERBYsys_id"
        offset = 0
        yielded = 0
        while True:
            params = {"sysparm_query": query, "sysparm_limit": page_size, "sysparm_offset": offset}
            if fields:
                params["sysparm_fields"] = ",".join(fields)
            rows = self._request("GET", f"/api/now/table/{table}", params=params).json().get("result", [])
            for row in rows:
                yield row
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if len(rows) < page_size:
                return
            offset += page_size

    def create_record(self, table: str, data: dict) -> dict:
        return self._request("POST", f"/api/now/table/{table}", json=data).json()["result"]

    def update_record(self, table: str, sys_id: str, data: dict) -> dict:
        return self._request("PATCH", f"/api/now/table/{table}/{sys_id}", json=data).json()["result"]

    def upsert(self, table: str, key_field: str, data: dict) -> tuple[str, dict]:
        """Create or update by a business key. Returns ("created" | "updated", record)."""
        key = data[key_field]
        existing = list(self.get_records(table, query=f"{key_field}={key}", fields=["sys_id"], limit=2))
        if len(existing) > 1:
            raise ServiceNowError(f"{len(existing)} records in {table} share {key_field}={key}; refusing to update")
        if existing:
            return "updated", self.update_record(table, existing[0]["sys_id"], data)
        return "created", self.create_record(table, data)
