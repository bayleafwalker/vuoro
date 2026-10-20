"""Record the declared families of a park/release/reclaim. Never choose one."""
import hashlib
import json

from .model_family import classify
from .parking import OPERATION, validate_context
from .record_tools import build_toolset as record_toolset
from .toolsets import ToolFailure


async def observe_reclaim(context, runs, result, parsed, forwarded):
    """Observation is best effort; the already acquired lease always stands."""
    try:
        authorities = getattr(forwarded.identity, "authorities", frozenset())
        if not {"work:read", "work:evidence"} <= authorities:
            raise ToolFailure("reclaim-observation-unavailable", "recording reclaim facts requires work:read and work:evidence")
        if not await runs.operation_available(OPERATION):
            raise ToolFailure("reclaim-observation-unavailable", "the owner does not advertise bounded parking history")
        lease = result["lease"]
        facts = validate_context(await runs.shell_client.invoke(
            OPERATION, {"item_id": parsed["item_id"], "lease_id": lease["lease_id"]}, forwarded
        ), repo_id=forwarded.repo_id, item_id=parsed["item_id"])
        prior = facts["previous"]
        current = facts["lease_run"]
        if prior is None or prior["rate_limit"] is None:
            return result
        if current is None or current["run_id"] != parsed["run_id"]:
            raise ToolFailure("parking-owner-invalid", "the lease's model declaration names another run")
        previous_family = classify(prior["model_id"])
        family = classify(current["model_id"])
        observation = {
            "schema": "vuoro-parked-reclaim/v1", "parked_event_id": prior["event_id"],
            "parked_reason_ref": prior["reason_ref"], "predecessor_run_id": prior["run_id"],
            "predecessor_model_id": prior["model_id"], "predecessor_model_family": previous_family,
            "model_id": current["model_id"], "model_family": family,
            "same_family": (family == previous_family if "unclassified" not in (family, previous_family) else None),
        }
        digest = "sha256:" + hashlib.sha256(json.dumps(observation, sort_keys=True, separators=(",", ":"),
                                                      ensure_ascii=False).encode()).hexdigest()
        tools = record_toolset(context)
        if tools is None:
            raise ToolFailure("reclaim-observation-unavailable", "the evidence record surface is unavailable")
        append = next(tool for tool in tools.tools if tool.name == "append_evidence")
        arguments = {
            "run_id": parsed["run_id"], "kind": "parked_reclaim", "ref": "work-lease:" + lease["lease_id"],
            "digest": digest, "collector": "vuoro-mcp-edge/e4@v1",
            "validity": {"basis": "indefinite", "valid_from": lease["acquired_at"], "valid_until": None,
                         "component_digests": {}},
            "claims": [{"claim_type": "observation", "subject": f"item:{parsed['item_id']}"}],
            "provenance": {"observation": observation}, "idempotency_key": "parked-reclaim:" + lease["lease_id"],
        }
        item = await append.run(append.parse(arguments), forwarded)
        return {**result, "reclaim_observation": item}
    except ToolFailure as failure:
        return {**result, "warnings": [*result.get("warnings", []),
                {"code": failure.code, "message": failure.message}]}
