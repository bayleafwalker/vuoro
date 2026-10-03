"""Neutral resource outcomes; test reference only, never a runtime executor."""
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json


def canonical(value):
    def validate(node):
        if isinstance(node, float):
            raise ValueError("floating point is outside the canonical resource contract")
        if isinstance(node, dict):
            if not all(isinstance(key, str) for key in node):
                raise ValueError("canonical object keys must be strings")
            for child in node.values():
                validate(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                validate(child)
    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


@dataclass(frozen=True)
class Resource:
    resource_id: str
    creator: str
    revision: int
    state: str = "registered"
    relations: tuple[tuple[str, str], ...] = ()
    evidence_digest: str | None = None
    acceptance: tuple[str, int, str, str] | None = None
    settlements: tuple[str, ...] = ()


def action_resource_ownership(resources, principal):
    """Derived immutable ownership view; actor names and grants confer nothing.

    Input is an authoritative snapshot of resources, not work-item assignees,
    leases, acceptance reviewers or an independently writable ownership table.
    """
    return tuple(sorted(row.resource_id for row in resources if row.creator == principal))


class ResourceReference:
    def __init__(self):
        self.rows = {}
        self.changes = {}
        self.change_digests = {}
        self.decisions = {}
        self.response_digests = {}
        self.conflicts = {}
        self.evidence = {}

    def command(self, resource_id, operation, arguments, *, principal, roles, key, expected):
        request = {"resource_id": resource_id, "operation": operation, "arguments": arguments, "expected": expected}
        digest = sha256(canonical(request)).hexdigest()
        binding = ("environment", principal, operation, key)
        if binding in self.decisions:
            prior_digest, response = self.decisions[binding]
            if prior_digest == digest:
                return response
            conflict_key = (*binding, digest)
            return self.conflicts.setdefault(conflict_key, canonical({"status": "rejected", "code": "key-conflict", "message": "key-conflict", "resource_id": resource_id, "before": self.rows[resource_id].revision if resource_id in self.rows else 0, "after": None, "first_binding_digest": prior_digest}))
        row = self.rows.get(resource_id)
        try:
            required_role = {"create": "creator", "relation": "relation-writer", "evidence": "evidence-ingester", "accept": "acceptance-reviewer", "reject": "acceptance-reviewer", "settle": "reconciler", "supersede": "owning-superseder"}[operation]
            if required_role not in roles:
                raise ValueError("authority")
            if operation == "create":
                if "creator" not in roles:
                    raise ValueError("authority")
                if row is not None or expected != 0:
                    raise ValueError("revision")
                updated = Resource(resource_id, principal, 1)
            else:
                if row is None or row.revision != expected:
                    raise ValueError("revision")
                if row.state == "superseded":
                    raise ValueError("state")
                required = {"relation": "relation-writer", "evidence": "evidence-ingester", "accept": "acceptance-reviewer",
                            "reject": "acceptance-reviewer", "settle": "reconciler", "supersede": "owning-superseder"}[operation]
                if required not in roles:
                    raise ValueError("authority")
                if operation in {"relation", "supersede"} and principal != row.creator:
                    raise ValueError("owner")
                updated = replace(row, revision=row.revision + 1)
                if operation == "relation":
                    kind, target = arguments["kind"], arguments["target"]
                    if kind not in {"parent-of", "depends-on", "derived-from", "supersedes"} or target == resource_id or target not in self.rows:
                        raise ValueError("relation")
                    if kind in {"parent-of", "depends-on"}:
                        pending = [target]
                        visited = set()
                        while pending:
                            current = pending.pop()
                            if current == resource_id:
                                raise ValueError("cycle")
                            if current not in visited:
                                visited.add(current)
                                pending.extend(t for k, t in self.rows[current].relations if k in {"parent-of", "depends-on"})
                    updated = replace(updated, relations=row.relations + ((kind, target),))
                elif operation == "evidence":
                    if row.state not in {"registered", "evidence-recorded"}:
                        raise ValueError("state")
                    evidence_digest = arguments["digest"]
                    content = self.evidence.get(evidence_digest)
                    if content is None or sha256(content).hexdigest() != evidence_digest:
                        raise ValueError("evidence")
                    updated = replace(updated, state="evidence-recorded", evidence_digest=evidence_digest)
                elif operation == "accept":
                    if row.state != "evidence-recorded" or arguments["digest"] != row.evidence_digest:
                        raise ValueError("acceptance-binding")
                    content = self.evidence.get(row.evidence_digest)
                    if content is None or sha256(content).hexdigest() != row.evidence_digest:
                        raise ValueError("evidence")
                    updated = replace(updated, state="accepted", acceptance=(resource_id, row.revision, row.evidence_digest, principal))
                elif operation == "reject":
                    if row.state not in {"registered", "evidence-recorded"}:
                        raise ValueError("state")
                    updated = replace(updated, state="rejected")
                elif operation == "settle":
                    if row.state not in {"accepted", "rejected"}:
                        raise ValueError("state")
                    updated = replace(updated, settlements=row.settlements + (arguments["fact"],))
                elif operation == "supersede":
                    updated = replace(updated, state="superseded")
            self.rows[resource_id] = updated
            self.changes.setdefault(resource_id, []).append(updated)
            self.change_digests.setdefault(resource_id, []).append(sha256(canonical(asdict(updated))).hexdigest())
            response = canonical({"status": "accepted", "resource_id": resource_id, "before": expected, "after": updated.revision})
        except ValueError as error:
            response = canonical({"status": "rejected", "code": str(error), "message": str(error), "resource_id": resource_id, "before": row.revision if row else 0, "after": None})
        self.decisions[binding] = (digest, response)
        # The response bytes and their digest are distinct durable decision facts
        # in a real owner; this test model stores bytes and exposes their digest.
        self.response_digests[binding] = sha256(response).hexdigest()
        return response


def rebuild(changes, digests):
    """Fail closed on missing, duplicate or identity-conflicting journal facts."""
    rows = list(changes)
    if not rows:
        raise ValueError("empty journal")
    if len(digests) != len(rows):
        raise ValueError("journal digest count")
    first = rows[0]
    for position, row in enumerate(rows, 1):
        if sha256(canonical(asdict(row))).hexdigest() != digests[position - 1]:
            raise ValueError("journal digest")
        if row.revision != position:
            raise ValueError("journal position")
        if row.resource_id != first.resource_id or row.creator != first.creator:
            raise ValueError("journal identity")
    return rows[-1]
