"""List the ten most recent active incidents from a Personal Developer Instance."""
import logging

from snclient import ServiceNowClient, Settings

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    client = ServiceNowClient(Settings.from_env())
    for inc in client.get_records(
        "incident", query="active=true^ORDERBYDESCsys_created_on", fields=["number", "short_description", "priority"], limit=10
    ):
        print(inc["number"], inc["priority"], inc["short_description"])
