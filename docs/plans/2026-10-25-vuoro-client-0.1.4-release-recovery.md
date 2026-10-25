# Vuoro client 0.1.4 release recovery

This is a scoped successor to the legacy Python package publisher for the
client release that also exercises the newly required contracts runtime wheel.
It preserves the publisher's build-once selection, complete owner test suite,
release contract validation, selected-wheel tag check, attestation, and draft
then publish sequence.

## Failed 0.1.3 attempt

The immutable `vuoro-client-v0.1.3` tag points at source commit
`3860825b8c78a2ff3a30b2709408ddddc99bece7`. Its publication attempt
[run 40203510472](https://github.com/bayleafwalker/vuoro/actions/runs/40203510472)
failed in the complete repository suite: 3,361 passed, 240 skipped, 14 failed,
and 109 errors. Contract compiler tests lacked `VUORO_CONTRACT_NODE`,
`VUORO_CONTRACT_PYTHON`, and `VUORO_CONTRACT_DECODER`; the distribution
boundary test also found no built `vuoro-contracts` wheel. This workflow change
does not retag 0.1.3 or treat that attempt as a published release.

The former publisher's exact source digest was
`8364554fb84763e6436c118db0a030fd655635660dfe19ff85bd0b8e4e90d919`.
Its initial byte-freeze acceptance is historical evidence; this successor adds
only the pinned contract toolchain preparation and required runtime wheel to
the complete candidate before the existing full release gates.

## Fresh release candidate

The package source version is advanced to the unused `0.1.4`; the failed
`0.1.3` tag and its receipts remain immutable. Before repository tests, the
publisher restores the qualified Python 3.13.16 archive and checks both its
archive and executable digests, prepares Node 24.21.0 / npm 11.19.0 from the
locked Nix toolchain, installs the locked contracts Python environment, checks
the Node/Python/Ruff artifacts against `tool-artifacts.json`, and exports the
strict decoder/compiler environment to subsequent steps. It builds the
`vuoro-contracts` runtime wheel exactly once alongside the other release
candidate wheels, before running the full repository suite. The generated SDK
boundary and contract runtime wheel checks therefore execute in the same
tag-triggered candidate environment, with no test exclusions.

The publisher pins the Ubuntu 24.04 runner, installs disposable PostgreSQL 16
before the suite, and sets both owner test profiles to require those binaries.
This keeps the service restore and workspace owner tests active in the release
gate rather than allowing missing PostgreSQL to turn them into skips.

Only the selected `vuoro-client` wheel matching the fresh tag is published.
The contracts runtime wheel remains a checked dependency in the candidate
closure and is also included in complete release conformance. No release is
created by this source change; root review, merge, and publication remain
separate actions.
