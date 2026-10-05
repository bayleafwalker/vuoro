"""The authored metadata contract, ``vuoro-knowledge/v1``.

Authored metadata lives with the document (Markdown frontmatter) or in the
owning repository's Git-tracked ``knowledge.toml`` sidecar. Nothing here is
derived: headings, digests, backlinks and revisions belong to the catalog,
which is a rebuildable projection.

Every field is a *claim made by a document about itself*. ``lifecycle:
ratified`` or ``maintainer: platform`` is recorded and validated for shape, but
it never grants authority. Question-scoped authority comes only from a
delegation (``delegates`` in a ratified document, or ``[[authorities]]`` in the
repository's reviewed ``knowledge.toml``).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA = "vuoro-knowledge/v1"

# Document purpose: what kind of statement the document makes. A proposal is
# never presented as a decision, an observation never as current guidance.
PURPOSES = (
    "decision",
    "specification",
    "runbook",
    "explanation",
    "observation",
    "proposal",
)

# Document lifecycle. Deliberately separate from component status (owned by a
# status authority such as the disposition register) and from evidence status
# (owned by observations and the work/evidence store).
LIFECYCLES = ("draft", "proposed", "ratified", "superseded", "withdrawn")

# Legacy ``status:`` values seen across the estate that map without judgement.
# Anything else is reported as unmapped rather than guessed.
LEGACY_STATUS = {
    "draft": "draft",
    "proposed": "proposed",
    "ratified": "ratified",
    "superseded": "superseded",
    "withdrawn": "withdrawn",
}

RELATION_KINDS = ("supersedes", "implements", "supported_by", "related")

APPLICABILITY_DIMENSIONS = ("repos", "components", "environments", "versions")

DOC_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*[a-z0-9]$")
QUESTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

KNOWN_FIELDS = frozenset(
    {
        "doc_id",
        "purpose",
        "lifecycle",
        "status",
        "title",
        "ratified_at",
        "ratified_by",
        "effective",
        "observed",
        "verified",
        "applies_to",
        "subjects",
        "supersedes",
        "implements",
        "supported_by",
        "related",
        "governing_decision",
        "delegates",
        "establishes",
        "maintainer",
        "facts",
        "path",
        "knowledge",
        # Estate conventions that predate v1 and are read as-is.
        "owner",
        "created_at",
        "last_verified",
        "superseded_by",
        "superseded_at",
    }
)

# Pre-v1 keys read as aliases: ``owner`` is a maintenance claim, not authority.
ALIASES = {"owner": "maintainer", "last_verified": "verified"}


@dataclass(frozen=True)
class Problem:
    """A contract finding. ``error`` blocks a valid catalog; ``warning`` does not."""

    severity: str
    code: str
    message: str
    doc_id: str | None = None
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.doc_id:
            out["doc_id"] = self.doc_id
        if self.location:
            out["location"] = self.location
        return out


@dataclass(frozen=True)
class Relation:
    kind: str
    target: str
    in_part: bool = False
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "target": self.target}
        if self.in_part:
            out["in_part"] = True
        if self.scope:
            out["scope"] = self.scope
        return out


@dataclass(frozen=True)
class Delegation:
    """``question`` is answered by ``source`` within ``scope``."""

    question: str
    source: str
    scope: dict[str, tuple[str, ...]]
    declared_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "source": self.source,
            "scope": {k: list(v) for k, v in sorted(self.scope.items())},
            "declared_by": self.declared_by,
        }


@dataclass(frozen=True)
class Establishes:
    """An observation's claim that a component was seen in a given state."""

    component: str
    state: str

    def to_dict(self) -> dict[str, str]:
        return {"component": self.component, "state": self.state}


@dataclass(frozen=True)
class FactsSpec:
    """How to read per-component status facts out of a structured register."""

    extractor: str
    question: str
    items: str
    key: str
    status: str
    decided: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor": self.extractor,
            "question": self.question,
            "items": self.items,
            "key": self.key,
            "status": self.status,
            "decided": self.decided,
        }


@dataclass
class Metadata:
    doc_id: str
    purpose: str | None
    lifecycle: str | None
    title: str | None = None
    lifecycle_source: str = "lifecycle"
    ratified_at: str | None = None
    ratified_by: str | None = None
    effective: str | None = None
    observed: str | None = None
    verified: str | None = None
    applies_to: dict[str, Any] = field(default_factory=dict)
    subjects: tuple[str, ...] = ()
    relations: tuple[Relation, ...] = ()
    governing_decision: str | None = None
    delegates: tuple[Delegation, ...] = ()
    establishes: tuple[Establishes, ...] = ()
    maintainer: str | None = None
    facts: FactsSpec | None = None
    # Back-reference only. Supersession takes effect from the superseding
    # document's ``supersedes``; this side is checked for agreement.
    superseded_by: tuple[str, ...] = ()
    legacy_status: str | None = None

    @property
    def v1(self) -> bool:
        """Opted into the v1 contract (declares a purpose), not just a legacy id."""

        return self.purpose is not None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "doc_id": self.doc_id,
            "purpose": self.purpose,
            "lifecycle": self.lifecycle,
            "lifecycle_source": self.lifecycle_source,
        }
        for key in (
            "title",
            "ratified_at",
            "ratified_by",
            "effective",
            "observed",
            "verified",
            "governing_decision",
            "maintainer",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.applies_to:
            out["applies_to"] = self.applies_to
        if self.subjects:
            out["subjects"] = list(self.subjects)
        if self.relations:
            out["relations"] = [r.to_dict() for r in self.relations]
        if self.delegates:
            out["delegates"] = [d.to_dict() for d in self.delegates]
        if self.establishes:
            out["establishes"] = [e.to_dict() for e in self.establishes]
        if self.facts is not None:
            out["facts"] = self.facts.to_dict()
        if self.superseded_by:
            out["superseded_by"] = list(self.superseded_by)
        if self.legacy_status is not None:
            out["legacy_status"] = self.legacy_status
        return out


def _date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value).strip()


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def _scope(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(k): _strings(v)
        for k, v in value.items()
        if k in ("repos", "components", "environments")
    }


def parse_metadata(raw: dict[str, Any], location: str) -> tuple[Metadata | None, list[Problem]]:
    """Normalize one authored record. Returns ``None`` when it is not opted in.

    A document participates only when it declares ``doc_id``. Nothing else is
    mandatory at parse time; validation decides which omissions matter.
    """

    problems: list[Problem] = []
    if "doc_id" not in raw:
        return None, problems
    doc_id = str(raw["doc_id"]).strip()

    def problem(severity: str, code: str, message: str) -> None:
        problems.append(Problem(severity, code, message, doc_id, location))

    if not DOC_ID.match(doc_id):
        # A pre-v1 placeholder id (``TBD``) is reported without failing the
        # catalog; a v1 document must be addressable.
        severity = "error" if raw.get("purpose") is not None else "warning"
        problem(severity, "invalid-doc-id", f"doc_id {doc_id!r} must match {DOC_ID.pattern}")

    raw = dict(raw)
    if isinstance(raw.get("last_verified"), dict):
        raw["last_verified"] = raw["last_verified"].get("date")
    for legacy, current in ALIASES.items():
        if legacy in raw and current not in raw:
            raw[current] = raw[legacy]
    unknown = sorted(set(raw) - KNOWN_FIELDS)
    if unknown and raw.get("purpose") is not None:
        problem("warning", "unknown-field", f"fields not in {SCHEMA}: {', '.join(unknown)}")

    purpose = raw.get("purpose")
    if purpose is not None:
        purpose = str(purpose)
        if purpose not in PURPOSES:
            problem("error", "invalid-purpose", f"purpose {purpose!r} not in {list(PURPOSES)}")

    lifecycle = raw.get("lifecycle")
    lifecycle_source = "lifecycle"
    if lifecycle is not None:
        lifecycle = str(lifecycle)
        if lifecycle not in LIFECYCLES:
            problem(
                "error", "invalid-lifecycle", f"lifecycle {lifecycle!r} not in {list(LIFECYCLES)}"
            )
        if "status" in raw and LEGACY_STATUS.get(str(raw["status"])) not in (None, lifecycle):
            problem(
                "error",
                "contradictory-lifecycle",
                f"legacy status {raw['status']!r} contradicts lifecycle {lifecycle!r}",
            )
    elif "status" in raw:
        legacy = str(raw["status"]).strip()
        lifecycle = LEGACY_STATUS.get(legacy)
        lifecycle_source = "legacy-status"
        if lifecycle is None:
            problem(
                "warning",
                "unmapped-legacy-status",
                f"legacy status {legacy!r} has no unambiguous lifecycle; declare `lifecycle`",
            )

    applies_to: dict[str, Any] = {}
    raw_applies = raw.get("applies_to") or {}
    if not isinstance(raw_applies, dict):
        problem("error", "invalid-applies-to", "applies_to must be a mapping")
        raw_applies = {}
    for key, value in raw_applies.items():
        if key not in APPLICABILITY_DIMENSIONS:
            problem("error", "invalid-applies-to", f"unknown applicability dimension {key!r}")
            continue
        if key == "versions":
            if not isinstance(value, dict):
                problem("error", "invalid-applies-to", "applies_to.versions must map component to spec")
                continue
            applies_to[key] = {str(k): str(v) for k, v in value.items()}
            for spec in applies_to[key].values():
                try:
                    parse_spec(spec)
                except ValueError as exc:
                    problem("error", "invalid-version-spec", str(exc))
        else:
            applies_to[key] = sorted(_strings(value))

    relations: list[Relation] = []
    for kind in RELATION_KINDS:
        entries = raw.get(kind) or []
        if isinstance(entries, (str, dict)):
            entries = [entries]
        for entry in entries:
            if isinstance(entry, dict):
                target = str(entry.get("doc") or entry.get("target") or "")
                relations.append(
                    Relation(kind, target, bool(entry.get("in_part")), entry.get("scope"))
                )
            else:
                relations.append(Relation(kind, str(entry)))
    for relation in relations:
        if not relation.target:
            problem("error", "invalid-relation", f"{relation.kind} entry has no target")
        if relation.target == doc_id:
            problem("error", "self-relation", f"{relation.kind} names the document itself")

    delegates: list[Delegation] = []
    for entry in raw.get("delegates") or []:
        if not isinstance(entry, dict) or not entry.get("question") or not entry.get("source"):
            problem("error", "invalid-delegation", "delegates entries need question and source")
            continue
        question = str(entry["question"])
        if not QUESTION_ID.match(question):
            problem("error", "invalid-delegation", f"question {question!r} is not an identifier")
        delegates.append(Delegation(question, str(entry["source"]), _scope(entry.get("scope")), doc_id))

    establishes: list[Establishes] = []
    for entry in raw.get("establishes") or []:
        if not isinstance(entry, dict) or not entry.get("component") or not entry.get("state"):
            problem("error", "invalid-establishes", "establishes entries need component and state")
            continue
        establishes.append(Establishes(str(entry["component"]), str(entry["state"])))

    facts = None
    raw_facts = raw.get("facts")
    if raw_facts is not None:
        if not isinstance(raw_facts, dict) or raw_facts.get("extractor") != "status-register/v1":
            problem("error", "invalid-facts", "facts.extractor must be 'status-register/v1'")
        else:
            facts = FactsSpec(
                extractor="status-register/v1",
                question=str(raw_facts.get("question", "component-status")),
                items=str(raw_facts.get("items", "items")),
                key=str(raw_facts.get("key", "key")),
                status=str(raw_facts.get("status", "status")),
                decided=raw_facts.get("decided"),
            )

    superseded_by = raw.get("superseded_by") or []
    if isinstance(superseded_by, str):
        superseded_by = [superseded_by]

    metadata = Metadata(
        doc_id=doc_id,
        purpose=purpose,
        lifecycle=lifecycle,
        title=raw.get("title"),
        lifecycle_source=lifecycle_source,
        ratified_at=_date(raw.get("ratified_at")),
        ratified_by=raw.get("ratified_by"),
        effective=_date(raw.get("effective")),
        observed=_date(raw.get("observed")),
        verified=_date(raw.get("verified")),
        applies_to=applies_to,
        subjects=_strings(raw.get("subjects")),
        relations=tuple(relations),
        governing_decision=raw.get("governing_decision"),
        delegates=tuple(delegates),
        establishes=tuple(establishes),
        maintainer=raw.get("maintainer"),
        facts=facts,
        superseded_by=tuple(str(s) for s in superseded_by),
        legacy_status=None if raw.get("status") is None else str(raw["status"]),
    )
    for key in ("ratified_at", "effective", "observed", "verified"):
        value = getattr(metadata, key)
        if value is not None and not ISO_DATE.match(value):
            problem("error" if metadata.v1 else "warning", "invalid-date", f"{key} {value!r} is not YYYY-MM-DD")
    return metadata, problems


# --- version specifiers -------------------------------------------------------

_SPEC = re.compile(r"^(==|!=|>=|<=|>|<)\s*([0-9][0-9A-Za-z.+-]*)$")


def version_key(version: str) -> tuple:
    """Order dotted versions numerically; a pre-release tail sorts before release.

    ``0.1.0-poc.33`` < ``0.1.0``; ``0.1.9`` < ``0.1.10``. This is deliberately
    simple and documented, not a PEP 440 implementation.
    """

    main, _, pre = version.partition("-")
    nums = tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.+]", main))
    pre_key = tuple(int(p) if p.isdigit() else p for p in re.split(r"[.]", pre)) if pre else ()
    return (nums, 0 if pre else 1, tuple(str(p).zfill(12) for p in pre_key))


def parse_spec(spec: str) -> list[tuple[str, str]]:
    clauses = []
    for clause in spec.split(","):
        clause = clause.strip()
        match = _SPEC.match(clause)
        if not match:
            raise ValueError(f"version spec clause {clause!r} is not <op><version>")
        clauses.append((match.group(1), match.group(2)))
    return clauses


def satisfies(version: str, spec: str) -> bool:
    have = version_key(version)
    for op, want in parse_spec(spec):
        target = version_key(want)
        ok = {
            "==": have == target,
            "!=": have != target,
            ">=": have >= target,
            "<=": have <= target,
            ">": have > target,
            "<": have < target,
        }[op]
        if not ok:
            return False
    return True
