"""Read-only E4 facts. No release, timer or consumption estimator."""
from datetime import datetime, timedelta, timezone
import re

from .toolsets import ToolFailure

OPERATION = "work.lease.parking-context-v1"
REFERENCE = re.compile(r"vuoro-evidence:(run_[0-9A-HJKMNP-TV-Z]{26})/seq/(0|[1-9][0-9]{0,15})\Z")


def evidence_reference(value: str, run_id: str) -> int:
    match = REFERENCE.fullmatch(value)
    if not match or int(match[2]) > 9007199254740991:
        raise ToolFailure("reason-ref-unresolved", "the evidence reference is not an exact run/sequence reference")
    if match[1] != run_id:
        raise ToolFailure("reason-ref-foreign-run", "the evidence reference must name the reporting run")
    return int(match[2])


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("a UTC timestamp is required")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def validate_context(value, *, repo_id: str, item_id: int) -> dict:
    if (not isinstance(value, dict) or value.get("repo_id") != repo_id
            or value.get("item_id") != item_id
            or set(value) != {"repo_id", "item_id", "current", "previous", "lease_run", "evaluated_at"}):
        raise ToolFailure("parking-owner-invalid", "the parking result does not match the requested work")
    try:
        timestamp(value["evaluated_at"])
        for field in ("current", "previous"):
            park = value[field]
            if park is None:
                continue
            fields = {"event_id", "reason_ref", "parked_at", "report_id", "lease_id", "run_id", "model_id", "rate_limit"}
            if field == "previous":
                fields |= {"release_event_id", "released_at"}
            if not isinstance(park, dict) or set(park) != fields:
                raise ValueError("invalid parking shape")
            if type(park["event_id"]) is not int or park["event_id"] < 1:
                raise ValueError("invalid parking event")
            timestamp(park["parked_at"])
            for key in ("reason_ref", "report_id", "lease_id", "run_id", "model_id"):
                if park[key] is not None and not isinstance(park[key], str):
                    raise ValueError("invalid parking scalar")
            if field == "previous":
                timestamp(park["released_at"])
                if type(park["release_event_id"]) is not int or park["release_event_id"] <= park["event_id"]:
                    raise ValueError("invalid release event")
            limit = park["rate_limit"]
            if limit is not None:
                expected = {"observed_at", "status", "limit_scope", "resets_at", "reset_source", "http_status", "overage"}
                if (not isinstance(limit, dict) or set(limit) != expected or limit["status"] != "rejected"
                        or limit["limit_scope"] not in ("plan", "family", "unknown")
                        or limit["reset_source"] not in ("harness-structured", "unparsed-local-string", "absent")
                        or (limit["overage"] is not None and type(limit["overage"]) is not bool)
                        or (limit["http_status"] is not None and
                            (type(limit["http_status"]) is not int or not 100 <= limit["http_status"] <= 599))):
                    raise ValueError("invalid rate limit projection")
                timestamp(limit["observed_at"])
                if limit["resets_at"] is not None:
                    timestamp(limit["resets_at"])
                evidence_reference(park["reason_ref"], park["run_id"])
                if not isinstance(park["model_id"], str) or not park["model_id"]:
                    raise ValueError("missing model declaration")
        run = value["lease_run"]
        if run is not None and (not isinstance(run, dict) or set(run) != {"run_id", "model_id"}
                                or any(not isinstance(v, str) or not v for v in run.values())):
            raise ValueError("invalid lease run")
    except (ValueError, TypeError, KeyError, ToolFailure) as exc:
        raise ToolFailure("parking-owner-invalid", "the owner returned malformed parking facts") from exc
    return value


def current_projection(context: dict) -> dict | None:
    park = context["current"]
    if park is None:
        return None
    rate = park["rate_limit"]
    refillable = False
    if rate is not None:
        threshold = (timestamp(rate["resets_at"]) if rate["resets_at"] is not None
                     else timestamp(park["parked_at"]) + timedelta(hours=24))
        refillable = timestamp(context["evaluated_at"]) >= threshold
    return {**park, "refillable": refillable}
