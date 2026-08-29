# Recovery note — 2026-08-29

This package was found untracked with every `.py` file deleted.  Only the
`__pycache__` bytecode (CPython 3.12, 2026-08-28 20:02) and one built wheel
survived.  The sources below were recovered and are now committed.

## Library sources — exact, verified

All nine modules under `src/vuoro_evidence/` are byte-exact recoveries.
Verification: recompiling each restored file with CPython 3.12 and the
package's absolute path produces a marshalled code object **identical** to the
surviving `.pyc`.

- Eight modules came verbatim from the wheel
  (`src/vuoro_evidence/core/dist/vuoro-evidence/vuoro_evidence-0.1.0-py3-none-any.whl`)
  and already matched their `.pyc`.
- `ingress/hostproto.py` did **not**: the wheel predates the last edit.  It was
  reconstructed from the bytecode — adding `session_log()` (MCP/A2A session
  replay) and `decode(..., confirm=)` — until the compiled output matched the
  `.pyc` byte for byte, including the line and column table.

## Reconstructed from metadata, not exact

`pyproject.toml` (from the wheel's `METADATA`/`WHEEL` plus sibling-package house
style) and `README.md` (from the wheel's long description).  Both reproduce the
wheel's declared contents; neither is a byte recovery.

## Tests: partly rewritten, partly lost

The original `tests/` is lost.  Its `.pyc` are pytest-assertion-rewritten, and the recorded
traffic the tests replay (a Chromium session, a debugpy session, and a real
2026-08-08 outctl capture spool) is gone with no other copy in this repo.
What survived is the intent, read out of the bytecode:

| module | asserts |
| --- | --- |
| `test_boundary` | zero profile/adapter/host vocabulary in `core/`; `core` does not import `ingress` |
| `test_generic_paths` | nine generic reducer situations: stale target, precondition refusal, capability unavailable, persisted evidence, completed/failed grant consumption, uncertain outcome demanding observation, observation confirm/contradict, bounded-window expiry with reason, attributable input-change expiry, no-evidence reacquire |
| `test_ingress` | two registered profiles; receipt→completed-with-freshness; error→stale and not-invoked; unknown outcome reconciled by observation; real capture blocks blind rerun in-window and expires outside; timed-out capture is uncertain |
| `test_real_traffic` | browser and debugger sessions replayed through HostProto ingress into the same reducer as the command-capture lane |
| `test_binding_agnostic` | the debugpy correlator is reused verbatim for Delve; the MCP session loader is reused unchanged for an A2A-carried run |

Three modules were **rewritten from that intent** on 2026-08-29 and pass
(18 tests).  They are new source, not recoveries:

- `test_boundary.py` — both original assertions, restored in full.  It needs no
  fixtures, and it is the guard the package's own docstring promises.
- `test_generic_paths.py` — all nine situations, over hand-built items.  No
  ingress edge is imported, so a pass is real evidence that the core expresses
  these generically.
- `test_ingress.py` — the HostProto lane only.  Outctl was retired by owner
  ruling on 2026-08-29 and the `command-capture` decoder was removed with it, so
  the original's two capture cases are gone rather than kept passing against dead
  code; git history retains both.  `test_session_log_replays_a_recorded_session`
  is new, covering the function that only existed in bytecode.

`test_real_traffic` and `test_binding_agnostic` are **not** rewritten.  Both
assert properties *of recorded traffic* — that one correlator serves debugpy and
Delve alike, and that the MCP session loader needs no change for an A2A carrier.
Synthetic fixtures cannot establish either claim; rewriting them requires
re-recording with the adapters' `scripts/record-session.mts`.  That script is not
in this repo but **does survive**, in four sibling repos — `hostproto-dap-debugpy`,
`hostproto-dap-delve`, `hostproto-mcp-playwright` and `hostproto-a2a-worker` — so
these two modules are re-recordable.  Until someone re-records,
`session_log`'s carrier-agnosticism is asserted by design and unproven by test.
