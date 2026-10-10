"""Exercise the installed declared-change preview under the service UID.

This is intentionally image-local: host unit tests can find Git even when the
runtime image cannot. No database, repository checkout, or credential is used.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from sprintctl.effect_intent import canonical_intent_digest
from sprintctl.effect_preview import preview_intent


DIFF = """--- a/docs/demo.txt
+++ b/docs/demo.txt
@@ -1 +1 @@
-before
+after
"""


def _intent(diff: str) -> dict[str, object]:
    intent: dict[str, object] = {
        "intent_id": "intent_" + "0" * 26,
        "revision": 1,
        "state": "proposed",
        "item_id": 1,
        "repository": "qualification",
        "base_commit": "a" * 40,
        "release_digest": None,
        "title": "qualification",
        "rationale": "image parser check",
        "unified_diff": diff,
        "acceptance": None,
        "application": None,
    }
    intent["canonical_intent_digest"] = canonical_intent_digest(intent)
    return intent


def main() -> None:
    assert os.geteuid() == 65532, "image probe must run as the service UID"

    valid = _intent(DIFF)
    redacted = preview_intent(valid)
    assert redacted["declared_changes"] == {
        "status": "parsed", "path_count": 1, "paths": [],
        "redacted": True, "truncated": False,
    }, "installed Git parser must read one path while withholding it by default"
    assert redacted["authorization"].startswith("none;")
    assert redacted["external_consequences"] == {"status": "unknown"}

    disclosed = preview_intent(valid, disclose_paths=True)
    assert disclosed["declared_changes"]["paths"] == [{
        "path": "docs/demo.txt", "added_lines": 1,
        "removed_lines": 1, "source_only": False,
    }], "explicitly entitled path display must reflect the declared diff"

    invalid = preview_intent(_intent("not a unified diff\n"))
    assert invalid["declared_changes"]["status"] == "unsupported"
    assert invalid["declared_changes"]["paths"] == []

    with patch.dict(os.environ, {"PATH": "/nonexistent"}):
        missing_git = preview_intent(valid)
    assert missing_git["declared_changes"]["status"] == "unavailable"
    assert missing_git["declared_changes"]["paths"] == []

    print("declared preview image: parsed, redacted, and fail-closed")


if __name__ == "__main__":
    main()
