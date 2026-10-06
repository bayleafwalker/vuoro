# vuoro-knowledge

`vuoro-knowledge` resolves documentation and records where it came from,
over knowledge authored in Git. The design and its boundaries are in
`docs/architecture/knowledge-resolution.md`.

- **Contract** (`vuoro-knowledge/v1`): `doc_id`, `purpose`, `lifecycle`,
  `applies_to`, dates, `supersedes`, `delegates`, `establishes`. Write it in
  Markdown frontmatter or in the repository's `knowledge.toml` sidecar.
  Pre-v1 `status:`, `owner` and `last_verified` are read where the meaning is
  unambiguous.
- **Catalog**: a rebuildable projection with sections, links, SHA-256, Git
  commit and blob, and dirty state. It is never an editable copy.
- **Operations**: `search`, `get` (exact revision or bounded section, with
  notices) and `resolve_context` (applicable sources, question-scoped
  authorities, approved / intended / evidenced component states, conflicts and
  unresolved conditions).
- **Binding**: a reproducible context manifest, bound to work through
  Sprintctl's existing `artifact` evidence ref or a `work.evidence.append-v1`
  item draft. `recheck` reports changes that need review at resume or
  acceptance time.

```bash
uv run --package vuoro-knowledge vuoro-knowledge validate --root . --root ../kctl --root ../agentops
uv run --package vuoro-knowledge vuoro-knowledge search "kctl knowledge" --root . --root ../kctl
uv run --package vuoro-knowledge vuoro-knowledge get vuoro-agentic-estate --section 1-purpose-and-how-to-read-it --root .
uv run --package vuoro-knowledge vuoro-knowledge resolve --root . --root ../kctl --root ../agentops \
    --subject knowledge-resolution --out /tmp/context.json --item-id ev-1
uv run --package vuoro-knowledge vuoro-knowledge recheck /tmp/context.json --root . --root ../kctl --root ../agentops
```

Exit codes: `0` ok; `1` validation errors; `2` usage error or unknown
document; `3` from `recheck` when a governing source, guidance or a conflict
changed and the work needs an explicit refresh.

The package reads only the checkouts passed with `--root` and writes nothing
to them. It holds no work, review or publication state. It depends on PyYAML
for frontmatter and registers.
