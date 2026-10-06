"""Provider-neutral effect authority: INV-E1 and acceptance != settlement.

The reference binds actual InMemoryIntentStore; PostgreSQL binds served owner
operations from the immutable pin. A configured PG binding is mandatory.
All cases are essential safety; no executor, shared tenant or network effect.
"""
import os
import pytest
from authority_reference import ReferenceEffects


PROVIDERS = ["reference"] + (["sprintctl-pg"] if os.environ.get("VUORO_AUTHORITY_TEST_PG_URL") else [])


@pytest.fixture(params=PROVIDERS)
def authority(request):
    if request.param == "sprintctl-pg":
        from authority_pg_binding import PgEffectsBinding
        binding = PgEffectsBinding(os.environ["VUORO_AUTHORITY_TEST_PG_URL"])
    else:
        binding = ReferenceEffects()
    try:
        yield binding
    finally:
        binding.close()


def refused(code, action):
    with pytest.raises(Exception) as error:
        action()
    assert getattr(error.value, "code", None) == code


def test_propose_does_not_accept_or_settle(authority):
    row = authority.propose()
    assert row["state"] == "proposed" and row["acceptance"] is None
    assert authority.item_status() == "pending"
    refused("effect-invalid-transition", lambda: authority.transition("mark-applied", row))
    assert authority.get(row["intent_id"])["state"] == "proposed"


def test_accept_binds_exact_revision_digest_but_is_not_application_or_settlement(authority):
    row = authority.propose()
    accepted = authority.transition("accept", row)
    assert accepted["state"] == "accepted" and accepted["application"] is None
    assert accepted["acceptance"]["intent_id"] == row["intent_id"]
    assert accepted["acceptance"]["intent_revision"] == row["revision"]
    assert accepted["acceptance"]["canonical_intent_digest"] == row["canonical_intent_digest"]
    assert authority.item_status() == "pending", "acceptance never settles work"
    applied = authority.transition("mark-applied", accepted)
    assert applied["state"] == "applied" and applied["application"]["commit_sha"] == "b" * 40
    assert applied["application"]["pr_url"] == "https://forge.example/repo/pulls/1"
    assert authority.item_status() == "pending"


@pytest.mark.parametrize("operation", ["accept", "reject", "mark-applied"])
@pytest.mark.parametrize("field,code", [("revision", "effect-revision-mismatch"),
                                        ("canonical_intent_digest", "effect-digest-mismatch")])
def test_every_transition_refuses_wrong_acceptance_binding(authority, operation, field, code):
    row = authority.propose()
    if operation == "mark-applied":
        row = authority.transition("accept", row)
    before = authority.get(row["intent_id"])
    changed = {**row, field: row["revision"] + 1 if field == "revision" else "0" * 64}
    refused(code, lambda: authority.transition(operation, changed))
    assert authority.get(row["intent_id"]) == before


@pytest.mark.parametrize("authorities", [set(), {"work:write"}, {"work.effect.propose"}, {"work.effect.get"}])
def test_accept_requires_its_separate_capability(authority, authorities):
    row = authority.propose()
    refused("authority-required", lambda: authority.transition("accept", row, authorities=authorities))
    assert authority.get(row["intent_id"])["state"] == "proposed"


def test_accepted_content_is_immutable_and_change_requires_new_proposal(authority):
    accepted = authority.transition("accept", authority.propose())
    refused("effect-immutable", lambda: authority.try_edit(accepted))
    assert authority.get(accepted["intent_id"]) == accepted
    changed = authority.propose(title="changed")
    assert changed["intent_id"] != accepted["intent_id"]
    assert changed["canonical_intent_digest"] != accepted["canonical_intent_digest"]
    assert changed["state"] == "proposed" and changed["acceptance"] is None


def test_rejection_cannot_later_be_accepted(authority):
    rejected = authority.transition("reject", authority.propose())
    assert rejected["state"] == "rejected"
    refused("effect-invalid-transition", lambda: authority.transition("accept", rejected))
    assert authority.item_status() == "pending"



def test_oracle_detects_false_settlement_on_acceptance():
    class SettlingOnAccept(ReferenceEffects):
        def transition(self, operation, row, **kwargs):
            result = super().transition(operation, row, **kwargs)
            if operation == "accept":
                self.work_item["status"] = "done"
            return result
    with pytest.raises(AssertionError, match="acceptance never settles work"):
        test_accept_binds_exact_revision_digest_but_is_not_application_or_settlement(SettlingOnAccept())



@pytest.mark.parametrize("terminal,next_operation", [("rejected", "mark-applied"), ("applied", "accept")])
def test_terminal_effect_cannot_be_reaccepted_or_applied(authority, terminal, next_operation):
    row = authority.propose()
    if terminal == "rejected":
        row = authority.transition("reject", row)
    else:
        row = authority.transition("mark-applied", authority.transition("accept", row))
    refused("effect-invalid-transition", lambda: authority.transition(next_operation, row))
    assert authority.get(row["intent_id"])["state"] == terminal


PATCH = "--- a/readme.txt\n+++ b/readme.txt\n@@ -1 +1 @@\n-old\n+accepted\n"


def test_changed_patch_cannot_reuse_approval(authority):
    accepted = authority.transition("accept", authority.propose(unified_diff=PATCH))
    # Change only the patch bytes: title, rationale, repository and base stay fixed.
    changed = authority.propose(unified_diff=PATCH.replace("+accepted", "+changed"))
    assert changed["intent_id"] != accepted["intent_id"]
    assert changed["canonical_intent_digest"] != accepted["canonical_intent_digest"]
    assert changed["state"] == "proposed" and changed["acceptance"] is None
    before = authority.get(changed["intent_id"])
    stale_binding = {**changed, "canonical_intent_digest": accepted["canonical_intent_digest"]}
    refused("effect-digest-mismatch", lambda: authority.transition("accept", stale_binding))
    refused("effect-digest-mismatch", lambda: authority.transition("mark-applied", stale_binding))
    refused("effect-invalid-transition", lambda: authority.transition("mark-applied", changed))
    assert authority.get(changed["intent_id"]) == before
    assert authority.get(accepted["intent_id"]) == accepted
    assert authority.item_status() == "pending"
    # Positive control: an independent acceptance of the new digest is possible.
    newly_accepted = authority.transition("accept", changed)
    assert newly_accepted["acceptance"]["canonical_intent_digest"] == changed["canonical_intent_digest"]
    assert authority.item_status() == "pending"


def test_changed_patch_oracle_detects_ignored_acceptance_digest():
    class IgnoringDigest(ReferenceEffects):
        def transition(self, operation, row, **kwargs):
            row = {**row, "canonical_intent_digest": self.get(row["intent_id"])["canonical_intent_digest"]}
            return super().transition(operation, row, **kwargs)

    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        test_changed_patch_cannot_reuse_approval(IgnoringDigest())


@pytest.mark.parametrize("operation", ["accept", "mark-applied"])
def test_public_http_cannot_reach_protected_effect_transition(authority, operation):
    from effect_public_binding import EffectHttpBinding

    row = authority.propose()
    if operation == "mark-applied":
        row = authority.transition("accept", row)
    before = authority.get(row["intent_id"])
    boundary = EffectHttpBinding(authority)
    assert "work.effect." + operation not in boundary.public_authorities
    denied = boundary.transition(operation, row)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "authority-required"
    assert boundary.calls == [], "public refusal must precede any owner invocation"
    assert authority.get(row["intent_id"]) == before
    assert authority.item_status() == "pending"
    # Positive control proves that the registered path works for protected rights.
    allowed = boundary.transition(operation, row, public=False)
    assert allowed.status_code == 200, allowed.text
    assert boundary.calls == ["work.effect." + operation + "-v1"]
    assert authority.get(row["intent_id"])["state"] == (
        "accepted" if operation == "accept" else "applied")
    assert authority.item_status() == "pending"


def test_public_http_oracle_detects_missing_pre_owner_gate(monkeypatch):
    from dataclasses import replace
    from effect_public_binding import EffectHttpBinding

    original_init = EffectHttpBinding.__init__

    def without_shell_authority(self, authority):
        original_init(self, authority)
        for name, registered in list(self.registry._operations.items()):
            if name == "work.effect.mark-applied-v1":
                self.registry._operations[name] = replace(registered, definition=
                    registered.definition.model_copy(update={"required_authority": ""}))

    monkeypatch.setattr(EffectHttpBinding, "__init__", without_shell_authority)
    # The owner still refuses; the oracle detects the earlier horizon was skipped.
    with pytest.raises(AssertionError, match="public refusal must precede"):
        test_public_http_cannot_reach_protected_effect_transition(ReferenceEffects(), "mark-applied")
