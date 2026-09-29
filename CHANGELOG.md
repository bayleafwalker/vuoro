# Changelog

All notable changes to the separately versioned Vuoro distributions will be
recorded here.

## Unreleased

- vuoro-service 0.1.80 (hotfix line from 0.1.72, work schema 16): the work
  adapter is repinned to sprintctl 0.7.5 (sprintctl#2110), whose served
  PostgreSQL runtime connection survives database restarts: it reconnects
  before dispatch, replays only pure reads and unique-key commands after a
  loss, and `/health/ready` probes the database instead of reporting a
  remembered flag. `/health/ready` can take up to the 5 s connect timeout
  while the database is unreachable, so probes need `timeoutSeconds` above
  that. Main's next vuoro-service release is 0.1.81.

- Bootstrap the public repository and independent `vuoro-client` and
  `vuoro-service` package boundaries.
- Add protocol-v1 handshake, ETag catalog, safe JSON Schema registration,
  identity-derived generic invocation, and dynamic operation discovery.
