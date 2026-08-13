# vuoro-schema-runtime

`vuoro-schema-runtime` is the small, standard-library-only shared contract
layer for central schema assets. It owns immutable migration metadata,
identifier validation/quoting, digest calculation, SQL placeholder rendering,
and pure compatibility reports. It has no database connection protocol and
never executes SQL or DDL. Domain owners retain migration runners, drivers,
roles, and domain migration assets.
