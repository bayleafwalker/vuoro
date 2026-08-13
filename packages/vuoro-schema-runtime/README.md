# vuoro-schema-runtime

`vuoro-schema-runtime` is the small, standard-library-only shared runtime for
central PostgreSQL schema contracts. It owns migration metadata, identifier
validation, compatibility inspection, and the fail-closed runtime gate. It
does not import a database driver and it never runs from service startup
unless an owner explicitly calls the migration API.

The connection argument is deliberately structural: it must provide the
cursor and transaction methods used by the selected operation. This keeps
database-driver choice, domain tables, and migration assets in each owning
repository.
