# Generation source supply inventory

`python3 scripts/contracts/supply_inventory.py` emits a deterministic inventory
of the current generation source locks. It requires Python 3.11 or newer and
uses only the standard library. It neither installs nor launches generation
tools, queries a registry, changes a lock, or updates the generator's existing
qualified-input hashes.

The inventory first checks every source input against
`generate.py::_QUALIFIED_INPUT_SHA256`, parsed as data without importing the
generator. It records that generator's digest, the qualified input digests,
the local `@vuoro/contracts` source file inventory, all npm lock locations and
their registry integrity fields, all Python registry packages and their locked
source/wheel hashes, and the existing runtime source pins. Direct dependency
versions must agree with the project manifests. Missing or altered qualified
inputs, missing dependency artifact hashes, duplicate Python package identities,
and source symlinks refuse. The existing installed-artifact manifest is bound
by its source digest; this command does not re-verify installed artifacts.

Capture an inventory and compare it without rewriting either file:

```bash
python3 scripts/contracts/supply_inventory.py > /tmp/generation-source-supply.json
python3 scripts/contracts/supply_inventory.py --check /tmp/generation-source-supply.json
python3 -m unittest discover -s packages/vuoro-contracts/tests -p test_supply_inventory.py
```

The command constructs the complete result before writing stdout. A refusal
exits with status 2 and a fixed diagnostic, with no inventory on stdout. A
shell redirection may still create an empty destination before invocation;
check the exit status before using a capture. `--check` compares exact bytes
with an existing inventory. That comparison detects drift from a caller-held
snapshot; a snapshot is not a signature or an independent trust anchor.

The fixture changes a locked compiler version and observes refusal before
output, then restores the original source bytes and obtains the identical
inventory. Other controls remove source inputs and hashes, alter direct
versions, insert duplicate identities, and replace inputs with symlinks. This
qualifies locked-source refusal and exact restoration in the tested fixtures.
It does **not** run or qualify a generation-chain upgrade or rollback. A real
upgrade still requires new exact artifacts, independent review of revised
pins, real generation and compatibility checks, and restoration of the prior
chain and generated outputs.

Licence strings are copied only where the npm lock declares them; absent
declarations and Python lock licences remain unknown. These strings do not
establish licence compatibility, provenance authenticity, support lifecycle,
or legal permission. Support, cold bootstrap, actual installed artifact
verification, paid-service requirements, and generation-chain upgrade/rollback
remain explicitly unestablished. Q3 and the broader EC-00 acceptance stay open.
