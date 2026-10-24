import asyncio

from vuoro_client import AsyncVuoroClient, Profile


def test_operator_request_methods_are_thin_catalog_invocations():
    client = AsyncVuoroClient(Profile(name="test", endpoint="http://test", credential_ref="token:test"),
                              lambda _: "synthetic-token")
    calls = []

    async def invoke(operation, arguments, **kwargs):
        calls.append((operation, arguments, kwargs))
        return {"accepted": True}

    client.invoke = invoke

    async def run():
        try:
            await client.publish_operator_request(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS",
                packet={"schema": "operator-request/v1"},
                dependants=[{"work_item_id": 17, "session_id": "ses-a"}],
                idempotency_key="publish-request-1",
                repo_id="project-a",
            )
            await client.get_operator_request("req_01JBC3D4E5F6G7H8J9K0MNPQRS", repo_id="project-a")
            await client.list_operator_requests(limit=20, state="open", repo_id="project-a")
            await client.list_operator_request_events(
                "req_01JBC3D4E5F6G7H8J9K0MNPQRS", after_revision=2, limit=15, repo_id="project-a"
            )
            await client.revise_operator_request(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=2,
                packet={"schema": "operator-request/v1"}, idempotency_key="revise-request-1",
                repo_id="project-a",
            )
            await client.add_operator_request_dependant(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=3,
                dependant={"work_item_id": 17, "session_id": "ses-b"},
                idempotency_key="attach-dependant-1", repo_id="project-a",
            )
            await client.withdraw_operator_request_dependant(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=4,
                work_item_id=17, session_id="ses-a", idempotency_key="withdraw-dependant-1",
                repo_id="project-a",
            )
            await client.record_operator_request_progress(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=5,
                work_item_id=17, session_id="ses-a", step_id="check", status="completed",
                note="Digest matched.", idempotency_key="progress-request-1", repo_id="project-a",
            )
            await client.report_operator_request_performed(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=6,
                outcome="performed", note="Operator procedure completed.",
                idempotency_key="report-request-1", repo_id="project-a",
            )
            await client.attest_operator_request(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=7,
                statement="I observed the required physical operation complete.",
                performed_at="2026-10-24T10:30:00Z", idempotency_key="attest-request-1",
                repo_id="project-a",
            )
            await client.decline_operator_request(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=8,
                reason="The proposed procedure is no longer appropriate.",
                idempotency_key="decline-request-1", repo_id="project-a",
            )
            await client.supersede_operator_request(
                request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRS", expected_revision=9,
                replacement_request_id="req_01JBC3D4E5F6G7H8J9K0MNPQRT",
                packet={"schema": "operator-request/v1"}, idempotency_key="supersede-request-1",
                repo_id="project-a",
            )
        finally:
            await client.aclose()

    asyncio.run(run())

    assert [call[0] for call in calls] == [
        "work.request.publish-v1",
        "work.request.get-v1",
        "work.request.list-v1",
        "work.request.events-v1",
        "work.request.revise-v1",
        "work.request.dependent-add-v1",
        "work.request.dependent-withdraw-v1",
        "work.request.progress-v1",
        "work.request.report-performed-v1",
        "work.request.attest-v1",
        "work.request.decline-v1",
        "work.request.supersede-v1",
    ]
    assert all(call[2] == {"repo_id": "project-a"} for call in calls)
    assert calls[0][1]["idempotency_key"] == "publish-request-1"
    assert calls[6][1]["expected_revision"] == 4
    assert calls[6][1]["session_id"] == "ses-a"
    assert calls[3][1] == {
        "request_id": "req_01JBC3D4E5F6G7H8J9K0MNPQRS", "after_revision": 2, "limit": 15,
    }
    assert calls[7][1]["status"] == "completed"
    assert calls[8][1]["outcome"] == "performed"
    assert calls[9][1]["performed_at"] == "2026-10-24T10:30:00Z"
    assert calls[10][1]["reason"] == "The proposed procedure is no longer appropriate."
    assert calls[11][1]["replacement_request_id"] == "req_01JBC3D4E5F6G7H8J9K0MNPQRT"
