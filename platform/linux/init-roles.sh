#!/bin/sh
# Runs once as the PostgreSQL bootstrap owner, never in the bot container.
set -eu
: "${APP_DATABASE_PASSWORD:?required}" "${MIGRATION_DATABASE_PASSWORD:?required}"
if [ "$APP_DATABASE_PASSWORD" = "$POSTGRES_PASSWORD" ] || \
   [ "$APP_DATABASE_PASSWORD" = "$MIGRATION_DATABASE_PASSWORD" ] || \
   [ "$MIGRATION_DATABASE_PASSWORD" = "$POSTGRES_PASSWORD" ]; then
    echo "Database roles require three different passwords" >&2
    exit 1
fi
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set ON_ERROR_STOP=on --set app_password="$APP_DATABASE_PASSWORD" \
    --set migration_password="$MIGRATION_DATABASE_PASSWORD" <<'SQL'
CREATE ROLE notebook LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE notebook_migrator LOGIN PASSWORD :'migration_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER DATABASE notebook_bot OWNER TO notebook_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO notebook;
ALTER DEFAULT PRIVILEGES FOR ROLE notebook_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO notebook;
ALTER DEFAULT PRIVILEGES FOR ROLE notebook_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO notebook;
SQL
