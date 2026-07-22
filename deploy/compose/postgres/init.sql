-- Compose-only disposable evaluation roles. These values are not deployment
-- credentials and must never be copied into an appservice overlay.
CREATE ROLE work_migration LOGIN PASSWORD 'local-evaluation-only';
CREATE ROLE work_runtime LOGIN PASSWORD 'local-evaluation-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE execution_migration LOGIN PASSWORD 'local-evaluation-only';
CREATE ROLE execution_runtime LOGIN PASSWORD 'local-evaluation-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE knowledge_migration LOGIN PASSWORD 'local-evaluation-only';
CREATE ROLE knowledge_runtime LOGIN PASSWORD 'local-evaluation-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE audit_migration LOGIN PASSWORD 'local-evaluation-only';
CREATE ROLE audit_runtime LOGIN PASSWORD 'local-evaluation-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

CREATE SCHEMA work AUTHORIZATION work_migration;
CREATE SCHEMA execution AUTHORIZATION execution_migration;
CREATE SCHEMA knowledge AUTHORIZATION knowledge_migration;
CREATE SCHEMA audit AUTHORIZATION audit_migration;

GRANT USAGE ON SCHEMA work TO work_runtime;
GRANT USAGE ON SCHEMA execution TO execution_runtime;
GRANT USAGE ON SCHEMA knowledge TO knowledge_runtime;
GRANT USAGE ON SCHEMA audit TO audit_runtime;
