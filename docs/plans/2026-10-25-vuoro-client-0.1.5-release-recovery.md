# Vuoro client 0.1.5 release recovery

This is a scoped successor to the reviewed 0.1.4 publisher integration. It
preserves both immutable failed release records and changes only the next fresh
client version plus the interpreter/workspace selection used by publisher
`uv run` commands.

## Failed 0.1.4 attempt

The `vuoro-client-v0.1.4` tag and publication run `40209690209` are preserved
as failed history. The run log shows `setup-uv` exported `UV_PYTHON=3.12`; the
later unqualified `uv run pytest` selected CPython 3.12, removed the previously
synchronized virtual environment, and installed only five packages. Collection
then stopped in `packages/vuoro-worker/tests/conftest.py` because `uvicorn` was
missing, before the repository suite ran. The run did not publish 0.1.4.

The earlier `vuoro-client-v0.1.3` publication failure (run `40203510472`) and
its compiler/wheel evidence remain recorded in [the 0.1.4 recovery
note](2026-10-25-vuoro-client-0.1.4-release-recovery.md).
That earlier publisher source had digest
`8364554fb84763e6436c118db0a030fd655635660dfe19ff85bd0b8e4e90d919`.
Neither immutable tag is retagged by this change.

## Fresh candidate

The next unused source version is 0.1.5. After verifying the pinned Python
3.13.16 executable and synchronizing every locked workspace package and extra,
the publisher exports that exact executable as `UV_PYTHON`. Every `uv run`
command also supplies `--python "$qualified_python" --all-packages --all-extras
--locked`, so test and release-validation commands retain the complete qualified
workspace rather than switching interpreters or rebuilding a smaller project
environment. The separate isolated consumer checks intentionally continue to
use Python 3.12 for compatibility coverage.

The publisher's candidate wheel build-once flow, compiler and decoder checks,
full repository gate, PostgreSQL 16 requirement, candidate conformance, selected
wheel tag check, attestation, and release sequence remain enabled. No tag or
release is created by this source change; root review, merge, and publication
remain separate actions.

## Local exact-environment qualification

The release candidate was synchronized with the pinned Python 3.13.16
interpreter, all 58 locked workspace packages and extras, and built once as the
seven publisher candidate wheels. With the pinned compiler, decoder, Node
artifacts, and PG16.15 binaries selected, the publisher-equivalent command
`uv run --locked --all-packages --all-extras --python "$qualified_python" pytest -q`
completed with 3,591 passed and 146 skips, with no failures. The skips are
existing fixture-gated owner/cgroup cases; this result does not claim their
separate runtime fixtures ran. The production compiler and MCP projection gates
passed (135 tests), and the release contract, fresh 0.1.5 tag check, and
isolated Python 3.12 served-consumer checks passed. Canonical PR checks remain
required before root publishes a fresh tag.
