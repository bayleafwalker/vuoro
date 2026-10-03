"""Essential neutral scenarios. Owner carrier qualification is separate."""
from hashlib import sha256
import json
import pytest
from .resource_contract import ResourceReference, action_resource_ownership, canonical


def command(model, resource, operation, expected, arguments=None, *, principal="issuer:subject:0", roles=None, key=None):
    roles = {"creator", "relation-writer", "evidence-ingester", "acceptance-reviewer", "reconciler", "owning-superseder"} if roles is None else roles
    return model.command(resource, operation, arguments or {}, principal=principal, roles=roles,
                         key=key or f"{resource}:{operation}:{expected}:{arguments}", expected=expected)


def created(*ids):
    model = ResourceReference()
    for resource in ids:
        assert json.loads(command(model, resource, "create", 0))["status"] == "accepted"
    return model


@pytest.mark.essential_safety
def test_identity_cas_rejection_does_not_change_projection_or_history():
    model = created("A")
    assert model.rows["A"].revision == 1
    before = model.rows["A"]
    rejected = command(model, "A", "supersede", 0, key="stale")
    assert json.loads(rejected) == {"status": "rejected", "code": "revision", "message": "revision", "resource_id": "A", "before": 1, "after": None}
    assert command(model, "A", "supersede", 0, key="stale") == rejected
    assert model.rows["A"] == before and model.changes["A"] == [before]
    assert all(model.response_digests[binding] == sha256(response).hexdigest() for binding, (_, response) in model.decisions.items())
    assert len(model.decisions) == 2


@pytest.mark.essential_safety
def test_relations_are_source_owned_target_unchanged_and_mixed_cycles_refused():
    model = created("A", "B", "C")
    target = model.rows["B"]
    assert json.loads(command(model, "A", "relation", 1, {"kind": "parent-of", "target": "B"}))["status"] == "accepted"
    assert model.rows["B"] == target
    command(model, "B", "relation", 1, {"kind": "depends-on", "target": "C"})
    before = model.rows["C"]
    assert json.loads(command(model, "C", "relation", 1, {"kind": "parent-of", "target": "A"}))["code"] == "cycle"
    assert model.rows["C"] == before
    assert json.loads(command(model, "C", "relation", 1, {"kind": "derived-from", "target": "C"}))["code"] == "relation"


@pytest.mark.essential_safety
def test_reissued_actor_does_not_inherit_source_ownership_or_projection():
    model = created("A", "B")
    assert action_resource_ownership(model.rows.values(), "issuer:subject:0") == ("A", "B")
    assert action_resource_ownership(model.rows.values(), "issuer:subject:1") == ()
    rejected = command(model, "A", "supersede", 1, principal="issuer:subject:1")
    assert json.loads(rejected)["code"] == "owner"
    assert model.rows["A"].state == "registered"


@pytest.mark.essential_safety
@pytest.mark.parametrize("operation,arguments", [("relation", {"kind": "derived-from", "target": "B"}),
    ("evidence", {"digest": "missing"}), ("accept", {"digest": "missing"}), ("reject", {}),
    ("settle", {"fact": "native-not-executed"}), ("supersede", {})])
def test_actor_role_denial_is_durable_and_does_not_mutate(operation, arguments):
    model = created("A", "B")
    before = model.rows["A"]
    rejected = command(model, "A", operation, 1, arguments, roles={"reader"}, key="denied")
    assert json.loads(rejected)["code"] == "authority"
    assert command(model, "A", operation, 1, arguments, roles={"reader"}, key="denied") == rejected
    assert model.rows["A"] == before


@pytest.mark.essential_workflow
def test_digest_bound_acceptance_is_distinct_from_local_settlement_and_supersession():
    model = created("A")
    bad = command(model, "A", "evidence", 1, {"digest": "missing"})
    assert json.loads(bad)["code"] == "evidence"
    content = "héllo".encode()
    digest = sha256(content).hexdigest()
    model.evidence[digest] = content
    command(model, "A", "evidence", 1, {"digest": digest})
    before = model.rows["A"]
    assert json.loads(command(model, "A", "accept", 2, {"digest": "wrong"}))["code"] == "acceptance-binding"
    assert model.rows["A"] == before
    command(model, "A", "accept", 2, {"digest": digest})
    accepted = model.rows["A"]
    assert accepted.acceptance == ("A", 2, digest, "issuer:subject:0")
    assert accepted.settlements == ()
    command(model, "A", "settle", 3, {"fact": "external-observation"})
    assert model.rows["A"].state == "accepted"
    command(model, "A", "supersede", 4)
    assert model.rows["A"].creator == accepted.creator
    assert json.loads(command(model, "A", "settle", 5, {"fact": "late"}))["code"] == "state"
    assert [row.revision for row in model.changes["A"]] == [1, 2, 3, 4, 5]
    assert model.changes["A"][-1] == model.rows["A"]


@pytest.mark.essential_safety
def test_command_first_binding_survives_response_loss_and_conflicting_digest():
    model = created("A")
    first = command(model, "A", "supersede", 1, key="once")
    assert command(model, "A", "supersede", 1, key="once") == first
    conflict = command(model, "A", "supersede", 2, key="once")
    assert json.loads(conflict)["code"] == "key-conflict"
    assert command(model, "A", "supersede", 2, key="once") == conflict
    assert command(model, "A", "supersede", 1, key="once") == first
    assert len(model.changes["A"]) == 2


@pytest.mark.essential_safety
def test_canonical_bytes_preserve_unicode_and_reject_floats():
    assert canonical({"z": "héllo", "a": 1}) == '{"a":1,"z":"héllo"}'.encode()
    with pytest.raises(ValueError, match="floating point"):
        canonical({"value": 1.2})


@pytest.mark.essential_safety
def test_rebuild_refuses_gaps_duplicate_positions_and_identity_conflicts():
    from dataclasses import asdict, replace
    from .resource_contract import rebuild
    model = created("A")
    command(model, "A", "supersede", 1)
    history = model.changes["A"]
    assert rebuild(history, model.change_digests["A"]) == model.rows["A"]
    for invalid, error in ((history[1:], "journal position"),
                           (history + [history[-1]], "journal position"),
                           ([history[0], replace(history[1], creator="issuer:subject:1")], "journal identity")):
        digests = [sha256(canonical(asdict(row))).hexdigest() for row in invalid]
        with pytest.raises(ValueError, match="^" + error + "$"):
            rebuild(invalid, digests)
    with pytest.raises(ValueError, match="^journal digest count$"):
        rebuild(history[1:], model.change_digests["A"])
    with pytest.raises(ValueError, match="^journal digest$"):
        rebuild(history, list(reversed(model.change_digests["A"])))


@pytest.mark.essential_safety
def test_duplicate_creation_and_corrupt_evidence_cannot_advance():
    model = created("A")
    before = model.rows["A"]
    assert json.loads(command(model, "A", "create", 0, key="another-create"))["code"] == "revision"
    digest = sha256(b"expected").hexdigest()
    model.evidence[digest] = b"corrupt"
    assert json.loads(command(model, "A", "evidence", 1, {"digest": digest}))["code"] == "evidence"
    assert model.rows["A"] == before


@pytest.mark.essential_workflow
def test_rejection_local_settlement_and_first_binding_are_separate():
    model = created("A")
    command(model, "A", "reject", 1, key="review")
    assert model.rows["A"].state == "rejected" and model.rows["A"].settlements == ()
    command(model, "A", "settle", 2, {"fact": "operator-observed"}, key="review")
    assert model.rows["A"].state == "rejected"
    assert model.rows["A"].settlements == ("operator-observed",)
    # Same key under another operation is a separate binding, not a collision.
    assert len(model.decisions) == 3
    assert json.loads(command(model, "A", "accept", 3, {"digest": "missing"}))["code"] == "acceptance-binding"


@pytest.mark.essential_safety
@pytest.mark.parametrize("availability", ["missing", "corrupt"])
def test_acceptance_rechecks_evidence_bytes_and_leaves_projection_unchanged(availability):
    model = created("A")
    digest = sha256(b"verified").hexdigest()
    model.evidence[digest] = b"verified"
    command(model, "A", "evidence", 1, {"digest": digest})
    before = model.rows["A"]
    if availability == "missing":
        del model.evidence[digest]
    else:
        model.evidence[digest] = b"changed"
    refusal = json.loads(command(model, "A", "accept", 2, {"digest": digest}))
    assert refusal == {"status": "rejected", "code": "evidence", "message": "evidence",
                       "resource_id": "A", "before": 2, "after": None}
    assert model.rows["A"] == before


@pytest.mark.essential_safety
def test_creator_denial_and_nonowner_relation_are_nonmutating():
    model = ResourceReference()
    assert json.loads(command(model, "A", "create", 0, roles={"reader"}))["code"] == "authority"
    assert model.rows == {} and model.changes == {}
    model = created("A", "B")
    before = model.rows["A"]
    refusal = command(model, "A", "relation", 1, {"kind": "derived-from", "target": "B"}, principal="issuer:subject:1")
    assert json.loads(refusal)["code"] == "owner"
    assert model.rows["A"] == before
