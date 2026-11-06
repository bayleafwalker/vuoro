# Vuoro agent declaration v2

This additive declaration version is defined by
[`agent-declaration-v2.schema.json`](agent-declaration-v2.schema.json). It
retains the seven required top-level fields and channel shape of
[`agent-declaration-v1.schema.json`](agent-declaration-v1.schema.json). Version
1 remains unchanged and continues to accept only `continue`.

This is a standalone declaration schema. The existing v1 registration and
directory operations remain bound to v1 schemas; this document does not bind v2
to an operation. A compatible operation contract and its read projection must
be defined before an implementation accepts or returns v2 declarations. The
standalone directory-entry v2 schema is described in
[`agent-directory-entry-v2.md`](agent-directory-entry-v2.md).

Version 2 allows an instance to declare acceptance of `continue` and/or
`request-resolution/v1` in `accepted_kinds`. Values must be unique, and the
array has the same maximum length of eight as v1. `channels` retains the v1
channel-name pattern and wake-mode vocabulary.

The OAuth Cloud receiver example below declares only
`request-resolution/v1` and an empty `channels` array. An empty array says no
channel or wake mode is declared. This declaration does not claim a wake
mechanism, authorize message actions, or establish that any receiver is
deployed or qualified. The schema version alone does not change the inbox
contract or create a receiver endpoint.

```json
{
  "schema": "agent-declaration/v2",
  "harness": {"name": "vuoro-cloud-oauth-receiver", "version": "1"},
  "adapter": null,
  "host_class": "cloud-interactive",
  "interactivity": "interactive",
  "accepted_kinds": ["request-resolution/v1"],
  "channels": []
}
```

