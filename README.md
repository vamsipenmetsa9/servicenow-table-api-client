# servicenow-table-api-client

A small Python client for the ServiceNow Table API that handles the parts integrations usually get wrong: OAuth token reuse and refresh, stable pagination, retry with backoff, and a safe upsert.

> **Portfolio project.** Written independently as a clean-room demonstration. It contains no employer or client code, configuration or data. All sample data is synthetic.

## Business use case

An external system (HR, asset inventory, monitoring) needs to read from and write to ServiceNow on a schedule. The integration has to survive expired tokens, rate limits and brief outages, must not create duplicate records, and must not leak credentials into logs.

## What it does

| Concern | Behavior |
|---|---|
| Authentication | OAuth 2.0 token from `/oauth_token.do`, cached until 30 seconds before expiry, refreshed once on a 401 |
| Pagination | `sysparm_limit` / `sysparm_offset` with `ORDERBYsys_id` added so pages stay stable while data changes |
| Retry | 429 and 5xx retried with exponential backoff; `Retry-After` is honored; capped by `SN_MAX_RETRIES` |
| Upsert | Match on a business key; create, update, or refuse when more than one record matches |
| Errors | `AuthError`, `RateLimitError`, `ServiceNowError` carry the HTTP status |

```mermaid
flowchart LR
  A[Source system job] --> B[ServiceNowClient]
  B -->|token, cached| C[/oauth_token.do/]
  B -->|GET, POST, PATCH with retry| D[/api/now/table/...]
  D --> E[(ServiceNow tables)]
```

## Stack

Python 3.11+, `requests`. Tests use only the standard library (`unittest`, `unittest.mock`).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in values for a Personal Developer Instance
set -a && . ./.env && set +a
python example.py
```

## Tests

```bash
python -m unittest discover -s tests -v
```

16 tests cover paging, token caching and refresh, backoff timing, exhausted retries, error messages and all three upsert outcomes. The HTTP session is injected, so no instance is needed.

## Security notes

- Credentials come from environment variables only; `.env` is git-ignored and `.env.example` holds no values.
- HTTPS is required for the instance URL.
- The token endpoint response body is never logged or put into exception text.
- Use a dedicated integration user with the minimum roles and ACLs for the tables involved.

## Limitations and next steps

- Uses the OAuth password grant because it works on a default PDI. Client credentials or JWT bearer would be the better choice where the instance supports them.
- No Batch API or Import Set API support yet; large loads should go through import sets and transform maps.
- Synchronous only.

## License

MIT
