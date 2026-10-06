"""Rebuildable catalog over Git-authored documents.

The catalog is a projection. It never becomes an editable copy of the
documentation: rebuilding it from the same checkouts yields the same digest,
and deleting it loses nothing that Git does not hold.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .contract import Delegation, Metadata, Problem, _scope, parse_metadata

CATALOG_SCHEMA = "vuoro-knowledge-catalog/v1"
REPO_CONFIG = "knowledge.toml"
REPO_SCHEMA = "vuoro-knowledge-repo/v1"
DEFAULT_INCLUDE = ("**/*.md",)
ALWAYS_EXCLUDE = (".git/**", ".venv/**", "**/node_modules/**", "**/.*/**")

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", slug)


@dataclass
class Section:
    slug: str
    heading: str
    level: int
    start_line: int  # 1-based, inclusive (the heading line)
    end_line: int  # 1-based, inclusive

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "heading": self.heading,
            "level": self.level,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass
class Document:
    metadata: Metadata
    repo: str
    path: str
    root: str
    sha256: str
    revision: str | None
    blob: str | None
    dirty: bool
    title: str
    sections: list[Section]
    links: list[str]
    text: str
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return self.metadata.doc_id

    def source_ref(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "path": self.path,
            "revision": self.revision,
            "blob": self.blob,
            "sha256": self.sha256,
            "dirty": self.dirty,
        }

    def to_dict(self, *, with_text: bool = True) -> dict[str, Any]:
        out = {
            "doc_id": self.doc_id,
            "title": self.title,
            "metadata": self.metadata.to_dict(),
            "source": self.source_ref(),
            "sections": [s.to_dict() for s in self.sections],
            "links": self.links,
        }
        if self.facts:
            out["facts"] = self.facts
        if with_text:
            out["text"] = self.text
        return out


@dataclass
class Subject:
    id: str
    title: str
    components: tuple[str, ...]
    repo: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "components": list(self.components), "repo": self.repo}


@dataclass
class Catalog:
    documents: dict[str, Document]
    delegations: list[Delegation]
    subjects: dict[str, Subject]
    roots: list[dict[str, Any]]
    problems: list[Problem]

    def digest(self) -> str:
        body = {
            "documents": [
                {"doc_id": d.doc_id, "metadata": d.metadata.to_dict(), "source": d.source_ref()}
                for d in sorted(self.documents.values(), key=lambda d: d.doc_id)
            ],
            "delegations": sorted((canonical(d.to_dict()) for d in self.delegations)),
            "subjects": [s.to_dict() for s in sorted(self.subjects.values(), key=lambda s: s.id)],
        }
        return "sha256:" + sha256_hex(canonical(body))

    def find(self, ref: str) -> Document | None:
        """Resolve a reference by doc_id, ``doc_id:fragment``, ``repo:path`` or a unique path suffix."""

        return self.locate(ref)[0]

    def locate(self, ref: str) -> tuple[Document | None, str | None]:
        """Like :meth:`find`, also returning a fragment (``doc_id:R1``, ``path#heading``)."""

        if ref in self.documents:
            return self.documents[ref], None
        head, colon, fragment = ref.partition(":")
        if colon and head in self.documents:
            return self.documents[head], fragment
        ref, _hash, anchor = ref.partition("#")
        doc = self._find_path(ref)
        return doc, (anchor or None) if doc else None

    def _find_path(self, ref: str) -> Document | None:
        if ref in self.documents:
            return self.documents[ref]
        repo, sep, path = ref.partition(":")
        candidates = []
        for doc in self.documents.values():
            if sep and doc.repo == repo and doc.path == path:
                return doc
            full = f"{doc.repo}/{doc.path}"
            if not sep and (doc.path == ref or full == ref or full.endswith("/" + ref)):
                candidates.append(doc)
        return candidates[0] if len(candidates) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CATALOG_SCHEMA,
            "builder": {"name": "vuoro-knowledge", "version": __version__},
            "digest": self.digest(),
            "roots": self.roots,
            "subjects": [s.to_dict() for s in sorted(self.subjects.values(), key=lambda s: s.id)],
            "delegations": [d.to_dict() for d in self.delegations],
            "documents": [d.to_dict() for d in sorted(self.documents.values(), key=lambda d: d.doc_id)],
            "problems": [p.to_dict() for p in self.problems],
        }


# --- git ----------------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def git_revision(root: Path) -> str | None:
    return _git(root, "rev-parse", "HEAD")


def git_blob(root: Path, revision: str | None, path: str) -> str | None:
    if revision is None:
        return None
    return _git(root, "rev-parse", f"{revision}:./{path}")


def git_hash_object(root: Path, path: str) -> str | None:
    return _git(root, "hash-object", path)


def git_show(root: Path, revision: str, path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:./{path}"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.decode()


# --- parsing ------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, int]:
    """Return (frontmatter, body, body_offset_lines)."""

    match = _FRONTMATTER.match(text)
    if not match:
        return None, text, 0
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, text, 0
    if not isinstance(data, dict):
        return None, text, 0
    return data, text[match.end():], text[: match.end()].count("\n")


def parse_sections(text: str) -> tuple[str | None, list[Section]]:
    lines = text.splitlines()
    sections: list[Section] = []
    title = None
    in_fence = False
    seen: dict[str, int] = {}
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        if title is None and level == 1:
            title = heading
        slug = slugify(heading)
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 0
        sections.append(Section(slug, heading, level, number, len(lines)))
    for index, section in enumerate(sections):
        for later in sections[index + 1:]:
            if later.level <= section.level:
                section.end_line = later.start_line - 1
                break
    return title, sections


def _lookup(data: Any, dotted: str | None) -> Any:
    if dotted is None:
        return None
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def extract_facts(metadata: Metadata, text: str) -> tuple[dict[str, dict[str, Any]], list[Problem]]:
    spec = metadata.facts
    if spec is None:
        return {}, []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return {}, [Problem("error", "facts-unreadable", f"cannot parse register: {exc}", metadata.doc_id)]
    items = _lookup(data, spec.items)
    if not isinstance(items, list):
        return {}, [Problem("error", "facts-unreadable", f"{spec.items!r} is not a list", metadata.doc_id)]
    facts: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get(spec.key) is None:
            continue
        decided = _lookup(item, spec.decided)
        facts[str(item[spec.key])] = {
            "status": None if _lookup(item, spec.status) is None else str(_lookup(item, spec.status)),
            "decided": None if decided is None else str(decided),
        }
    return facts, []


# --- building -----------------------------------------------------------------


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Git-style globs: ``**/`` spans zero or more directories, ``*`` stays in one."""

    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_glob_regex(pattern).match(path) for pattern in patterns)


_PRUNE = {"node_modules", "dist", "build", "__pycache__"}


def _walk(root: Path) -> list[Path]:
    files = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _PRUNE)
        files.extend(Path(directory) / name for name in filenames)
    return sorted(files)


def _load_repo_config(root: Path) -> tuple[dict[str, Any], list[Problem]]:
    config_path = root / REPO_CONFIG
    if not config_path.exists():
        return {}, []
    try:
        config = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        return {}, [Problem("error", "invalid-repo-config", str(exc), location=str(config_path))]
    if config.get("schema") != REPO_SCHEMA:
        return config, [
            Problem("error", "invalid-repo-config", f"schema must be {REPO_SCHEMA}", location=str(config_path))
        ]
    return config, []


def build_catalog(roots: list[Path | str]) -> Catalog:
    documents: dict[str, Document] = {}
    duplicates: dict[str, list[str]] = {}
    delegations: list[Delegation] = []
    subjects: dict[str, Subject] = {}
    root_records: list[dict[str, Any]] = []
    problems: list[Problem] = []

    for root in roots:
        root = Path(root).resolve()
        config, config_problems = _load_repo_config(root)
        problems.extend(config_problems)
        repo = str(config.get("repo") or root.name)
        revision = git_revision(root)
        root_records.append({"repo": repo, "root": str(root), "revision": revision})
        include = tuple(config.get("include") or DEFAULT_INCLUDE)
        exclude = tuple(config.get("exclude") or ()) + ALWAYS_EXCLUDE

        for subject in config.get("subjects") or []:
            sid = str(subject.get("id", ""))
            if not sid:
                problems.append(Problem("error", "invalid-subject", "subject without id", location=f"{repo}:{REPO_CONFIG}"))
                continue
            if sid in subjects:
                problems.append(Problem("error", "duplicate-subject", f"subject {sid!r} declared twice", location=f"{repo}:{REPO_CONFIG}"))
            subjects[sid] = Subject(sid, str(subject.get("title", sid)), tuple(subject.get("components") or ()), repo)

        for authority in config.get("authorities") or []:
            if not authority.get("question") or not authority.get("source"):
                problems.append(Problem("error", "invalid-delegation", "authorities entries need question and source", location=f"{repo}:{REPO_CONFIG}"))
                continue
            delegations.append(
                Delegation(
                    str(authority["question"]),
                    str(authority["source"]),
                    _scope(authority.get("scope")),
                    f"{repo}:{REPO_CONFIG}",
                )
            )

        records: list[tuple[str, dict[str, Any], str | None]] = []
        sidecar_paths = set()
        for entry in config.get("documents") or []:
            path = str(entry.get("path", ""))
            if not path or not (root / path).is_file():
                problems.append(
                    Problem("error", "sidecar-missing-file", f"sidecar names missing file {path!r}", entry.get("doc_id"), f"{repo}:{REPO_CONFIG}")
                )
                continue
            sidecar_paths.add(path)
            records.append((path, dict(entry), "sidecar"))

        for file in _walk(root):
            rel = file.relative_to(root).as_posix()
            if rel in sidecar_paths or _matches(rel, exclude) or not _matches(rel, include):
                continue
            if file.suffix.lower() not in (".md", ".markdown"):
                continue
            front, _body, _offset = split_frontmatter(file.read_text(errors="replace"))
            if front and "doc_id" in front:
                records.append((rel, front, None))

        for rel, raw, origin in records:
            location = f"{repo}:{rel}"
            if origin == "sidecar":
                text = (root / rel).read_text(errors="replace")
                front, _, _ = split_frontmatter(text)
                if front and "doc_id" in front:
                    problems.append(
                        Problem("error", "contradictory-declaration", "document has both frontmatter and a sidecar entry", raw.get("doc_id"), location)
                    )
            metadata, parse_problems = parse_metadata(raw, location)
            problems.extend(parse_problems)
            if metadata is None:
                continue
            content = (root / rel).read_bytes()
            text = content.decode(errors="replace")
            blob = git_blob(root, revision, rel)
            working = git_hash_object(root, rel) if blob else None
            heading_title, sections = (None, []) if not rel.endswith((".md", ".markdown")) else parse_sections(text)
            facts, fact_problems = extract_facts(metadata, text)
            problems.extend(fact_problems)
            doc = Document(
                metadata=metadata,
                repo=repo,
                path=rel,
                root=str(root),
                sha256=sha256_hex(content),
                revision=revision if blob else None,
                blob=blob,
                dirty=blob is None or working != blob,
                title=metadata.title or heading_title or Path(rel).stem,
                sections=sections,
                links=sorted(set(_LINK.findall(text))),
                text=text,
                facts=facts,
            )
            if metadata.doc_id in documents:
                duplicates.setdefault(metadata.doc_id, [f"{documents[metadata.doc_id].repo}:{documents[metadata.doc_id].path}"]).append(location)
                continue
            documents[metadata.doc_id] = doc
            delegations.extend(metadata.delegates)

    for doc_id, locations in sorted(duplicates.items()):
        problems.append(
            Problem("error", "duplicate-doc-id", f"doc_id declared at {', '.join(locations)}", doc_id)
        )
    return Catalog(documents, delegations, subjects, root_records, problems)
