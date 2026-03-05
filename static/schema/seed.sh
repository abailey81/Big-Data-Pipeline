#!/bin/sh
set -e

echo "Running schema creation..."
psql -h postgres-db -U postgres -d fift -f /seed/create_tables.sql

echo "Loading company_static data..."
psql -h postgres-db -U postgres -d fift -c "TRUNCATE systematic_equity.company_static;"
psql -h postgres-db -U postgres -d fift -c "\COPY systematic_equity.company_static(symbol, security, gics_sector, gics_industry, country, region) FROM '/seed/company_static.csv' WITH (FORMAT csv, HEADER true)"

echo "Verifying row count..."
psql -h postgres-db -U postgres -d fift -c "SELECT COUNT(*) AS company_count FROM systematic_equity.company_static;"

echo "Schema initialised and company_static seeded successfully."
