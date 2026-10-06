# Real GitHub API observation, 2026-10-06

This directory preserves the unaltered output body from:

```sh
gh api repos/bayleafwalker/vuoro/check-runs/112364306246
```

It records the successful `test` check for Vuoro PR182's exact source head
`ef636fcfa02f00fb8e96488da5b65e31e99c8c5d`. The capture metadata binds the file
bytes, request scope and file-persistence time. Git replication makes these
files cross-host retrievable; it does not authenticate execution or verify
GitHub's signature. No webhook was received, no response headers/framing were
captured, and no HTTP-byte or signature attestation is claimed.

The corresponding decoder labels this explicitly as a supplied REST response.
The check's head SHA is a commit reference, not an exact effect-artifact digest.
Build/session/rubric/instruction facts absent from the provider stay unknown.
The original response and canonical normalized-observation digests have
separate domains.

This is a real provider case for MI-1/P2. It is not yet an authoritative evidence
append: the native producer must bind an actual owner run, chain tail and
receipt. It does not prove protected acceptance, a receipt chain, a real hosted
predecessor or full Track B. Those references will be recorded separately when
verified; this source observation must not be rewritten into an acceptance.
