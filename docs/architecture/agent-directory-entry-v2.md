# Vuoro agent directory entry v2

This standalone schema is defined by
[`agent-directory-entry-v2.schema.json`](agent-directory-entry-v2.schema.json).
It keeps the entry fields and directory facts of
[`agent-directory-entry-v1.schema.json`](agent-directory-entry-v1.schema.json),
but its `declaration` field can contain either an `agent-declaration/v1` or
`agent-declaration/v2` value.

The v1 directory schema and the existing v1 list and describe contracts remain
unchanged. This schema does not bind itself to a list or describe operation.
The operation version, registration compatibility, and behavior for reading
records through older contracts must be resolved before an implementation
uses this schema as an operation result.
