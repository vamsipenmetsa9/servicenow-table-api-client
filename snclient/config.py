"""Settings are read from environment variables only. Nothing is hard-coded."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    instance_url: str
    client_id: str
    client_secret: str
    username: str
    password: str
    timeout: float = 30.0
    max_retries: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        required = ["SN_INSTANCE_URL", "SN_CLIENT_ID", "SN_CLIENT_SECRET", "SN_USERNAME", "SN_PASSWORD"]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError("Missing environment variables: " + ", ".join(missing))
        url = os.environ["SN_INSTANCE_URL"].rstrip("/")
        if not url.startswith("https://"):
            raise ValueError("SN_INSTANCE_URL must use https")
        return cls(
            instance_url=url,
            client_id=os.environ["SN_CLIENT_ID"],
            client_secret=os.environ["SN_CLIENT_SECRET"],
            username=os.environ["SN_USERNAME"],
            password=os.environ["SN_PASSWORD"],
            timeout=float(os.environ.get("SN_TIMEOUT", "30")),
            max_retries=int(os.environ.get("SN_MAX_RETRIES", "4")),
        )
