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
