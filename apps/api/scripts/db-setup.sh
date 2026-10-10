#!/usr/bin/env sh
# Create the YardLens database (if missing) and apply db/schema.sql with psql.
# Reads YARDLENS_DATABASE_URL from the environment or from apps/api/.env.
set -eu

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

url="${YARDLENS_DATABASE_URL:-postgresql+asyncpg://postgres:postgres@localhost:5432/yardlens}"

case "$url" in
  postgresql*) ;;
  *)
    echo "db-setup: YARDLENS_DATABASE_URL is not PostgreSQL; nothing to do."
    exit 0
    ;;
esac

if ! command -v psql >/dev/null 2>&1; then
  echo "db-setup: psql not found. Install the PostgreSQL client (e.g. 'brew install libpq')." >&2
  exit 1
fi

# libpq does not understand SQLAlchemy driver suffixes such as "+asyncpg".
pg_url=$(printf '%s' "$url" | sed -E 's#^postgresql\+[a-z0-9]+://#postgresql://#')
database=$(printf '%s' "$pg_url" | sed -E 's#^[^/]*//[^/]*/([^?]*).*$#\1#')
admin_url=$(printf '%s' "$pg_url" | sed -E 's#^([^/]*//[^/]*/)[^?]*#\1postgres#')

if [ -z "$database" ] || [ "$database" = "$pg_url" ]; then
  echo "db-setup: could not determine the database name from YARDLENS_DATABASE_URL" >&2
  exit 1
fi

exists=$(psql "$admin_url" -v ON_ERROR_STOP=1 -Atc "SELECT 1 FROM pg_database WHERE datname = '$database'")
if [ "$exists" != "1" ]; then
  psql "$admin_url" -v ON_ERROR_STOP=1 -qc "CREATE DATABASE \"$database\""
  echo "db-setup: created database '$database'"
fi

PGOPTIONS="--client-min-messages=warning" psql "$pg_url" -v ON_ERROR_STOP=1 -q -f db/schema.sql
echo "db-setup: schema applied to '$database'"
