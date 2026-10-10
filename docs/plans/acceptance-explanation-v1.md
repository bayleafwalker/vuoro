# Caller-bound acceptance explanation v1

This is the native transport slice of agentops#2637, after the Agentops-owned
operator-projection 0.2.5 release. It adds one advisory MCP read,
`explain_acceptance`, to the existing owner-read toolset. It does not introduce
an owner operation, evaluator, store, runner or second acceptance path.

The tool requires both `work.effect.get` and `work:read`; neither authority
alone lists or invokes it. Its only argument is an intent ID in the portable
presentation's bounded grammar. Repository, workspace and request identity
come from the verified gateway assertion. There is no credential, profile,
DSN or caller-supplied capture override.

Under that same caller, the edge reads exactly `work.effect.get-v1`, then
`work.read.release`, `work.read.item-decisions` and `work.lease.read-v1` if the
effect supplied an owner-valid item ID. Each shell call keeps the original
assertion/request binding and receives a fresh body-bound edge proof. Existing
owner authorities still apply individually. No write operation is invoked.

Catalog availability requires all four operation names with read execution
semantics. The invocation transport checks those semantics before sending a
read, including after its existing one stale-catalog refresh. A refreshed
catalog that omits the operation or marks it as a write refuses before a
retry POST. Each POST carries the exact catalog revision whose semantics were
checked, even if another request refreshes shared catalog metadata while that
POST is in flight. Existing ordinary invocation behavior is unchanged.

The edge builds an in-memory P1 capture, invokes the released owner's
`reconstruct(..., live=True)`, and emits only `portable_acceptance.present`
and `render_text` inside `vuoro-acceptance-explanation/v1`. The exact published
wheel URL and verified SHA-256 are pinned; no local-source or vendored fallback
is permitted. The normal MCP success wrapper is retained, with nested text.

Denied or unavailable owner reads become generic unavailable observations and
explicit missing links, never a claim that owner state is absent. Malformed,
foreign-repository or foreign-item responses refuse generically. Private raw
captures, reports, intent bytes, principals, contracts, histories and upstream
messages never leave in tool output, logs or errors. A complete P1 explanation
means link consistency. Currentness, current authorization of a saved artifact,
and owner authentication of the portable artifact remain explicitly unknown;
the four reads are non-atomic advice and authorize no effect.

Qualification must exercise conjunctive authority, assertion/request/workspace
and replay rejection, actual per-call proof handling, initial and refreshed
catalog write-class refusal, owner denial and private-marker cases, conflicting
artifact/Release bindings and a missing receipt. The built service image must
import the exact published projection package. Source and fixture qualification
do not establish live Cloud scopes, a refreshed grant, or real Claude/ChatGPT
rendering. Those remain deployment and client gates.
