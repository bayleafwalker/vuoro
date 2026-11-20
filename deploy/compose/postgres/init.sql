\set ON_ERROR_STOP on
\getenv work_migration_password VUORO_COMPOSE_WORK_MIGRATION_PASSWORD
CREATE ROLE work_migration LOGIN PASSWORD :'work_migration_password';
\getenv work_runtime_password VUORO_COMPOSE_WORK_RUNTIME_PASSWORD
CREATE ROLE work_runtime LOGIN PASSWORD :'work_runtime_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\getenv audit_migration_password VUORO_COMPOSE_AUDIT_MIGRATION_PASSWORD
CREATE ROLE audit_migration LOGIN PASSWORD :'audit_migration_password';
\getenv audit_runtime_password VUORO_COMPOSE_AUDIT_RUNTIME_PASSWORD
CREATE ROLE audit_runtime LOGIN PASSWORD :'audit_runtime_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

CREATE SCHEMA work AUTHORIZATION work_migration;
CREATE SCHEMA audit AUTHORIZATION audit_migration;

GRANT USAGE ON SCHEMA work TO work_runtime;
GRANT USAGE ON SCHEMA audit TO audit_runtime;

\getenv knowledge_migration_password VUORO_COMPOSE_KNOWLEDGE_MIGRATION_PASSWORD
CREATE ROLE knowledge_migration LOGIN PASSWORD :'knowledge_migration_password';
\getenv knowledge_runtime_password VUORO_COMPOSE_KNOWLEDGE_RUNTIME_PASSWORD
CREATE ROLE knowledge_runtime LOGIN PASSWORD :'knowledge_runtime_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE SCHEMA knowledge AUTHORIZATION knowledge_migration;
GRANT CREATE ON DATABASE vuoro TO work_migration, audit_migration, knowledge_migration;
GRANT USAGE ON SCHEMA knowledge TO knowledge_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE work_migration IN SCHEMA work
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO work_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE work_migration IN SCHEMA work
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO work_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE knowledge_migration IN SCHEMA knowledge
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO knowledge_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE knowledge_migration IN SCHEMA knowledge
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO knowledge_runtime;
