# Request continuation composition candidate

Client 0.1.6 exposes the transport-only continuation read. Service 0.1.101
composes the immutable Sprintctl 0.26.0 release from source
`bb6e83790e54a25b2367bff2eacd70cb4e8db611`, wheel SHA256
`77f2ef442bea00dbd1c3993e05fbd8b45c379106e3df8725f8f0766ca233e37a`.
The other four owner/shared release locks remain the composition's existing
published versions. The reference distribution follows these exact versions;
its candidate and qualification status remain unchanged.

The new owner operation reads a request, its immutable human-attestation basis
and current dependant work in one read-only repeatable-read transaction. Work
schema29 remains required; this release adds no migration. Service startup
checks compatibility and does not run owner DDL. A continuation-ready result
is context for an explicit subsequent decision. It does not acknowledge
consumption, wake a native session, or authorize an effect.

Publication uses new immutable tags for each distribution. The prior client
0.1.5 and service0.1.100 releases remain history. Owner publication and exact
wheel attestation precede service composition; native source and publisher
gates precede new client/service publication. Installed candidate-wheel and
live adoption evidence are recorded separately. Human pilot acceptance and
native wake remain outside this candidate's claims.

Qualification covers exact owner/shared wheel installation, old schema27/28
closure upgrade and refusal behavior, transport-only packaging, and the
continuation read over verified TLS against disposable unprivileged
PostgreSQL. Fault cases include unready reports, absent authority, foreign
workspace, invalid wire identity, changed dependant work, withdrawal and revoked
identity. Repeated reads must produce no event or delivery effect.
