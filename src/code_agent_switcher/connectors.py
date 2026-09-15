"""Which claude.ai connectors each account has, and what a switch costs.

A connector you added on claude.ai - Linear, Gmail, HubSpot - is authorised on
Anthropic's side against one account and organisation. Nothing about it is
stored on this machine: not a token, not a refresh token, not an expiry. So
switching accounts takes every one of them away, and there is nothing for this
app to copy across. People read that as the switcher breaking their MCP setup.
It is not. The account they moved to simply never authorised those connectors.

What the app can do is stop it being a surprise. The same OAuth token that reads
the usage figures also reads the account's connector list, so before a switch
happens it can say which connectors the other account does not have.

A local MCP server is the opposite case and needs no help here: its token lives
in the Keychain blob, which the switcher already carries to every account.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from code_agent_switcher import keychain
from code_agent_switcher.core import snapshot_service
from code_agent_switcher.usage import authorized_fetch

CONNECTORS_URL = "https://api.anthropic.com/v1/mcp_servers?limit=1000"
# Undocumented and beta: it can change or disappear without notice. Every caller
# treats a failure as "unknown", never as "this account has no connectors" -
# saying an account has nothing when the endpoint simply moved would be worse
# than saying nothing at all.
CONNECTORS_BETA = "mcp-servers-2025-12-04"
CONNECTORS_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class Connector:
    """One connector offered to an account, and whether it is usable."""

    name: str
    reason: str

    @property
    def connected(self) -> bool:
        """Authorised, including when its last call failed.

        `connected_with_error` is a connector whose authorisation is in place
        but currently failing. Counting it as missing would blame a switch for
        something that was already broken.
        """
        return self.reason.startswith("connected")

    @property
    def erroring(self) -> bool:
        return self.reason == "connected_with_error"


def _request_connectors(token: str) -> tuple[int, dict | None]:
    req = urllib.request.Request(
        CONNECTORS_URL,
        headers={
            "Accept": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": CONNECTORS_BETA,
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=CONNECTORS_TIMEOUT_SECONDS) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return 0, None


def _parse(payload: dict | None) -> tuple[Connector, ...]:
    rows = (payload or {}).get("data")
    if not isinstance(rows, list):
        return ()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("display_name")
        if not isinstance(name, str) or not name:
            continue
        reason = row.get("eligibility_reason")
        out.append(Connector(name=name, reason=reason if isinstance(reason, str) else ""))
    return tuple(out)


def fetch_connectors(service: str) -> tuple[tuple[Connector, ...] | None, str | None]:
    """Every connector offered to the account behind `service`, or why not."""
    payload, reason = authorized_fetch(service, _request_connectors)
    if payload is None:
        return None, reason
    return _parse(payload), None


def fetch_connectors_for_account(ref: str):
    return fetch_connectors(snapshot_service(ref))


def fetch_active_connectors():
    return fetch_connectors(keychain.CLAUDE_SERVICE)


def connected(connectors: tuple[Connector, ...] | None) -> tuple[str, ...]:
    """The names an account can actually use, in the order the API gave them."""
    if not connectors:
        return ()
    return tuple(c.name for c in connectors if c.connected)


def lost_by_switching(
    current: tuple[Connector, ...] | None, target: tuple[Connector, ...] | None
) -> tuple[str, ...]:
    """Connectors working now that the target account has not authorised.

    Returns nothing when either side is unknown. A warning built on a failed
    read would name connectors that are fine, and a warning nobody can trust is
    worse than no warning.
    """
    if current is None or target is None:
        return ()
    have = set(connected(target))
    return tuple(name for name in connected(current) if name not in have)
