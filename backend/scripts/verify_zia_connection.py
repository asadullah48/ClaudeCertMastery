"""Manual probe of the Zia Tutor AI MCP endpoint.

Run this to find out, concretely, whether the tutor is reachable from this machine and
whether the configured credential works:

    python scripts/verify_zia_connection.py

Exit codes: 0 usable, 1 reachable but unauthorized, 2 unreachable.

The distinction matters operationally. Unauthorized means fix the token; unreachable
means fix the network or the endpoint. Both leave the platform working -- the Ask Zia
panel simply hides itself.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.zia_client import ZiaTutorClient  # noqa: E402

EXPECTED_TOOLS = {
    "outline_agent_factory",
    "read_agent_factory_lesson",
    "search_agent_factory",
    "begin_session",
    "open_student_record",
    "get_teacher_context",
    "update_student_record",
}


async def main() -> int:
    print(f"Endpoint : {settings.zia_mcp_endpoint}")
    print(f"Token    : {'set' if settings.zia_mcp_token else 'NOT SET'}")
    print(f"Timeout  : {settings.zia_mcp_timeout_seconds}s\n")

    client = ZiaTutorClient(
        endpoint=settings.zia_mcp_endpoint,
        token=settings.zia_mcp_token,
        timeout=settings.zia_mcp_timeout_seconds,
    )

    print("Probing (initialize + tools/list)...")
    status = await client.probe()

    print(f"  reachable     : {status.reachable}")
    print(f"  authenticated : {status.authenticated}")
    print(f"  detail        : {status.detail}")
    if status.server_name:
        print(f"  server        : {status.server_name}")

    if not status.usable:
        if status.reachable:
            print("\nRESULT: reachable but unauthorized.")
            print("Set CERTMASTERY_ZIA_MCP_TOKEN to a bearer access token issued by")
            print("https://auth.panaversity.org (see README).")
            return 1
        print("\nRESULT: unreachable. The Ask Zia panel will hide itself.")
        return 2

    print(f"\n  tools ({len(status.tool_names)}): {', '.join(sorted(status.tool_names))}")
    missing = EXPECTED_TOOLS - set(status.tool_names)
    if missing:
        print(f"  WARNING: expected tools not advertised: {sorted(missing)}")

    print("\nLive call: search_agent_factory('prompt caching cost')...")
    result = await client.search("prompt caching cost economics", k=2)
    if result.ok and result.hits:
        for hit in result.hits:
            print(f"  - {hit.slug}  ({hit.heading_path})")
            print(f"    {hit.url}")
    else:
        print(f"  no hits: {result.detail or 'abstained'}")

    print("\nRESULT: usable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
