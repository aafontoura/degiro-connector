# Hardened fork

This is `aafontoura/degiro-connector`, branch `hardened`: a hardened fork of
[Chavithra/degiro-connector](https://github.com/Chavithra/degiro-connector),
based on the audited upstream commit
`35061e99ac3852b41b152b5d2a1b55287326f6f9` (version 3.0.36). It exists so
Denkeeper can read one DEGIRO account (trades, cash movements, portfolio and
cash balance) during a manual, attended sync without leaking the session or
the credentials.

The upstream BSD-3-Clause licence and copyright notice in `LICENSE` are kept
unchanged and apply to this fork.

## Supported surface

Only `degiro_connector.hardened` is supported:

```python
from datetime import date

from degiro_connector.hardened import OtpCredentials, ReadOnlyTradingClient

with ReadOnlyTradingClient() as client:
    client.login(OtpCredentials(username, password, "012345"))
    client.get_client_details()
    trades = client.get_transactions_history(date(2026, 1, 1), date(2026, 1, 31))
    cash = client.get_account_overview(date(2026, 1, 1), date(2026, 1, 31))
    balance = client.get_update(["cashFunds", "portfolio", "totalPortfolio"])
# close() has logged out and cleared the session
```

The rest of the package (the dynamic `trading.api.API` and the remaining
upstream trading actions) is upstream code with the H-1/H-2/H-3/H-4/H-5/H-9
fixes below applied where they touch it. Quotecast (`charting`/`quotecast`
hosts, its own session and token loader) is unchanged apart from H-1. Neither is
the supported surface.

## Patches

| ID | Problem at the audited commit | Change |
| --- | --- | --- |
| H-1 | Successful login logged at INFO with `sessionId`; errors logged response bodies and exception strings that contain URLs (and so the session ID) | `hardened` never logs. Upstream actions log only the exception type and HTTP status; the login success/error dumps are gone. The transport also raises `urllib3`/`requests` loggers above CRITICAL in its process. |
| H-2 | Errors carried raw responses and HTTP exception text | `DegiroError(code, status)`: an enumerated code and an int status, raised outside `except` blocks so no upstream exception is chained, and picklable by code and status. Upstream `LoginSuccess.session_id`, `LoginError.error` and `LoginError.in_app_token` are hidden from `repr`, so exception args no longer show them. |
| H-3 | OTP was an `int` (leading zero lost) chosen by truthiness (`000000` skipped) | `OtpCredentials` and upstream `Credentials.one_time_password` take exactly six ASCII digits as a string; branches use `is not None`. |
| H-4 | No request timeouts, including the maintenance probe | `hardened`: (5 s connect, 15 s read) per request plus a 15 s total checked between 8 KiB body chunks; (2, 5, 5) for logout. A trickling server can stretch one request a little past the total and DNS is not covered, so the caller's process needs its own wall-clock deadline. Upstream: login and maintenance probe (5, 15), logout (2, 5), any other request through `ModelSession` without a timeout gets (10, 60). |
| H-5 | Sessions followed redirects and trusted proxy/netrc/CA environment variables | `hardened`: `trust_env=False`, no proxies, no redirect is followed (any 3xx fails as `redirect_blocked`). Upstream `ModelSession` sessions: `trust_env=False`; login, logout and the maintenance probe (now sent through the caller's session) do not follow redirects. |
| H-6 | Any host reachable | `hardened` sends only to `https://trader.degiro.nl` on port 443 with no user info; there is no plain-HTTP adapter. |
| H-7 | `API` loads any action by name, orders included | `ReadOnlyTradingClient` exposes login, client details, transaction history, account overview, portfolio/cash update, product info and close; its transport allowlists each (method, path). Upstream `check_order`, `confirm_order`, `update_order` and `delete_order` actions and their URL constants are removed. |
| H-8 | Float and int pydantic models for money | `loads_decimal`: fractions become `Decimal` from their exact text, integers stay `int`, `NaN`/`Infinity`/duplicate keys are refused. `hardened` returns parsed JSON; the caller normalizes it. |
| H-9 | `build_credentials` read `DEGIRO_ACCOUNT` or a config file | Removed from the trading package; the examples keep a copy in `examples/credentials_from_config.py`. Upstream `Credentials` also treats empty secret fields as absent and now really refuses an OTP together with a TOTP secret. |
| H-10 | Logout left the session and credentials in memory | `close()` logs out within its budget, then clears the session ID, account ID, cookies, headers and HTTP session even if logout fails; it reports whether DEGIRO confirmed. Credentials are handed out once by `take()` and dropped. |

Releasing a reference is not erasing memory: CPython cannot erase a `str`.
The guarantee is that nothing is intentionally kept, logged or persisted.
Traceback frames of a raised `DegiroError` still hold local variables such as
the request URL, so a caller must pass only `code` and `status` across its
process boundary and must not use error reporters that capture locals.

## Tests

`tests/hardening/` covers every patch with a fake `requests` adapter and
synthetic values; no test touches the network. They are written for, and run
only in, a disposable container or an isolated host, never on a managed
workstation:

```sh
docker run --rm -v "$PWD":/src -w /src python:3.12-slim \
  sh -c 'pip install -e ".[dev]" && pytest tests/hardening -q'
```

Never use real DEGIRO credentials or account data in tests.

## Maintenance

Do not merge upstream changes automatically. On every upstream bump, review
the upstream diff (auth, session, logging, endpoints, dependencies), reapply
the patches, rerun `tests/hardening/` in isolation, and record the reviewed
upstream SHA and the new hardened SHA.
