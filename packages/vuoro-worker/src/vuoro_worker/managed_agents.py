"""`QueueClient` against the real Managed Agents self-hosted-sandbox polling
API (platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes).

**Not exercised in this repository's test suite.** It needs an operator
vendor account with a Managed Agents agent already registered, and this
task's boundary explicitly excludes registering one (see the PR/handoff
note). `poller.FakeQueueClient` is what tests and local dry runs use
instead; this class is the runnable, but not yet run, real-world adapter.

The exact polling request/response shape here follows the documented
outline (poll for a task, execute locally, post the result back) rather
than a verified wire trace, because no live credential exists to trace
against in this environment. Treat the endpoint paths and payload keys as
provisional until exercised once against a real registered agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from vuoro_worker.poller import ScheduledTask


class HTTPQueueClient:
    """Bearer-token client for Anthropic's Managed Agents polling endpoint.
    Structurally satisfies `poller.QueueClient` (poll/submit_result); not a
    subclass of the Protocol, matching how `FakeQueueClient` does the same.

    `base_url` and `token` come from operator-controlled configuration (an
    env var read by `cli.py`), never a literal in this module -- this class
    holds no credential of its own.
    """

    def __init__(self, *, base_url: str, token: str, agent_id: str, timeout_seconds: float = 30.0) -> None:
        self._agent_id = agent_id
        self._client = httpx.Client(
            base_url=base_url,
            headers={"authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def poll(self) -> ScheduledTask | None:
        response = self._client.get(f"/v1/managed-agents/{self._agent_id}/poll")
        if response.status_code == 204:
            return None
        response.raise_for_status()
        body = response.json()
        if not body:
            return None
        return ScheduledTask(
            task_id=body["task_id"],
            tool_name=body["tool_name"],
            arguments=body.get("arguments", {}),
        )

    def submit_result(self, task_id: str, result: Mapping[str, Any] | None, *, error: str | None = None) -> None:
        payload: dict[str, Any] = {"task_id": task_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = dict(result or {})
        response = self._client.post(
            f"/v1/managed-agents/{self._agent_id}/results", json=payload
        )
        response.raise_for_status()
