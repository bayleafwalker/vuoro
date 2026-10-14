"""Bounded, versioned, read-only diagnostic operations (``vuoro-ops``).

Each operation observes and classifies; none dispatches provider work,
redeems or seals an effect attempt, migrates a schema or connects to a live
database. Every result is one ``vuoro-diagnostic-result/v1`` document stamped
with the pinned composition it was computed against
(``docs/architecture/diagnostic-operations.md``).
"""

from .result import RESULT_SCHEMA_VERSION, DiagnosticResult, composition_stamp

__all__ = ["RESULT_SCHEMA_VERSION", "DiagnosticResult", "composition_stamp"]
