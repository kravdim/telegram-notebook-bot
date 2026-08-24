#!/bin/bash
set -euo pipefail

ROLE_NAME="dailyplanner_recovery"
TEMPLATE_DB="dailyplanner_recovery_template"
KEYCHAIN_SERVICE="dailyplanner-db-operator"
PSQL_BIN="${PSQL_BIN:-/opt/homebrew/opt/postgresql@17/bin/psql}"

if [ ! -x "$PSQL_BIN" ]; then
    echo "PostgreSQL psql not found: $PSQL_BIN" >&2
    exit 1
fi

keychain_created=0
if operator_secret=$(/usr/bin/security find-generic-password \
    -a "$ROLE_NAME" -s "$KEYCHAIN_SERVICE" -w 2>/dev/null); then
    :
else
    operator_secret=$(/usr/bin/openssl rand -hex 32)
    # Do this before touching PostgreSQL. If Keychain is locked or prompting is
    # unavailable, provisioning must fail without rotating the database role.
    /usr/bin/security add-generic-password \
        -U -a "$ROLE_NAME" -s "$KEYCHAIN_SERVICE" \
        -j "DailyPlanner CREATEDB-only recovery operator" \
        -T /usr/bin/security -w "$operator_secret" >/dev/null
    keychain_created=1
fi

stored_secret=$(/usr/bin/security find-generic-password \
    -a "$ROLE_NAME" -s "$KEYCHAIN_SERVICE" -w)
if [ "$stored_secret" != "$operator_secret" ]; then
    unset stored_secret
    echo "Keychain verification failed" >&2
    exit 1
fi
unset stored_secret

template_touched=0
template_ready=0

cleanup() {
    unset operator_secret
    if [ "$template_touched" = "1" ] && [ "$template_ready" != "1" ]; then
        "$PSQL_BIN" -d postgres -v ON_ERROR_STOP=1 -c \
            "UPDATE pg_database SET datistemplate=false, datallowconn=false WHERE datname='$TEMPLATE_DB'" \
            >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

{
    printf '%s\n' "DO \$\$ BEGIN"
    printf '%s\n' "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE_NAME') THEN"
    printf '%s\n' "CREATE ROLE $ROLE_NAME LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION;"
    printf '%s\n' "END IF; END \$\$;"
    printf 'ALTER ROLE %s WITH LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION;\n' \
        "$ROLE_NAME"
    printf 'GRANT CONNECT ON DATABASE postgres TO %s;\n' "$ROLE_NAME"
} | "$PSQL_BIN" -v ON_ERROR_STOP=1 -d postgres >/dev/null

template_exists=$("$PSQL_BIN" -d postgres -Atc \
    "SELECT count(*) FROM pg_database WHERE datname='$TEMPLATE_DB'")
if [ "$template_exists" = "0" ]; then
    "$(dirname "$PSQL_BIN")/createdb" --maintenance-db postgres \
        --template template0 "$TEMPLATE_DB"
fi
template_touched=1
"$PSQL_BIN" -d postgres -v ON_ERROR_STOP=1 -c \
    "UPDATE pg_database SET datallowconn=true WHERE datname='$TEMPLATE_DB'" >/dev/null
"$PSQL_BIN" -d "$TEMPLATE_DB" -v ON_ERROR_STOP=1 -c \
    "BEGIN;
     ALTER ROLE $ROLE_NAME SUPERUSER;
     SET ROLE $ROLE_NAME;
     DROP EXTENSION IF EXISTS vector CASCADE;
     DROP EXTENSION IF EXISTS pg_trgm CASCADE;
     DROP EXTENSION IF EXISTS pgcrypto CASCADE;
     CREATE EXTENSION vector;
     CREATE EXTENSION pg_trgm;
     CREATE EXTENSION pgcrypto;
     RESET ROLE;
     ALTER ROLE $ROLE_NAME NOSUPERUSER NOCREATEROLE NOREPLICATION;
     COMMIT" >/dev/null
"$PSQL_BIN" -d postgres -v ON_ERROR_STOP=1 -c \
    "UPDATE pg_database SET datistemplate=true, datallowconn=false WHERE datname='$TEMPLATE_DB'" >/dev/null
template_ready=1

sql_secret=${operator_secret//\'/\'\'}
printf "ALTER ROLE %s PASSWORD '%s';\n" "$ROLE_NAME" "$sql_secret" | \
    "$PSQL_BIN" -v ON_ERROR_STOP=1 -d postgres >/dev/null

echo "Recovery operator provisioned: role=$ROLE_NAME, template=$TEMPLATE_DB, keychain_service=$KEYCHAIN_SERVICE, keychain_created=$keychain_created"
