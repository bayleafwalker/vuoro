# Vuoro packaging overlay

## Closed boundaries

- Treat any database, migration, adapter, authority, or domain-core import in
  `vuoro-client` as a release-blocking architecture violation.
- Treat automatic migration during service import or startup as a
  release-blocking lifecycle violation.
- Verify migration and runtime roles independently once adapters are added.
- Reject catalog behavior that is authored in deployment overlays or varies by
  environment outside explicit configuration.
- Preserve Git canonicity for published knowledge content and local recovery
  records for offline audit capture.
