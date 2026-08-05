#!/usr/bin/env bash
set -e

# Load .env variables if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

if [ -z "$DATABASE_URL" ]; then
  echo "Error: DATABASE_URL is not set."
  exit 1
fi

echo "==> Resetting live Postgres public schema..."
psql "$DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

echo "==> Applying migrations/001_init.sql..."
psql "$DATABASE_URL" -f migrations/001_init.sql

echo "==> Schema successfully applied to Neon!"
