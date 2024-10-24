#!/usr/bin/env bash
set -eo pipefail

source .env # moderate hacks

execDBStatement() {
  if [[ $WRITE_DB_USE_SSL == "true" ]]; then
    SSL_ARGS="--set=sslmode=require"
  else
    SSL_ARGS=""
  fi
  echo "$1" | PGPASSWORD=$WRITE_DB_PASS psql \
    --host=$WRITE_DB_HOST \
    --port=$WRITE_DB_PORT \
    --username=$WRITE_DB_USER \
    --dbname=$INITIALLY_AVAILABLE_WRITE_DB \
    $SSL_ARGS
}

# await connected service availability
/scripts/await-service.sh $READ_DB_HOST $READ_DB_PORT $SERVICE_READINESS_TIMEOUT
/scripts/await-service.sh $WRITE_DB_HOST $WRITE_DB_PORT $SERVICE_READINESS_TIMEOUT

FULL_TEST_DB_NAME="${WRITE_DB_NAME}_test"
echo -e "\x1b[;93mRunning tests on '${FULL_TEST_DB_NAME}' database\x1b[m"

echo "Recreating database.."

echo "Dropping database ${FULL_TEST_DB_NAME}.."
execDBStatement "DROP DATABASE IF EXISTS ${FULL_TEST_DB_NAME}"

echo "Creating database ${FULL_TEST_DB_NAME}"
execDBStatement "CREATE DATABASE ${FULL_TEST_DB_NAME}"

# XXX:HACK: overwrite db names with test db names for runtime
export WRITE_DB_NAME=$FULL_TEST_DB_NAME
export READ_DB_NAME=$FULL_TEST_DB_NAME

echo "Running database migrations.."
/scripts/migrate-db.sh up

echo "Running database seeds.."
/scripts/seed-db.sh up

# make sure we're not running cache
find . -name "*.pyc" -delete
export PYTHONDONTWRITEBYTECODE=1

# use /srv/root as home
export PYTHONPATH=$PYTHONPATH:/srv/root
cd /srv/root

if [ "$APP_ENV" == "local" ]; then
  EXTRA_PARAMS="--pdb"
else
  EXTRA_PARAMS=""
fi

exec pytest tests/ \
    --cov-config=tests/coverage.ini \
    --cov=app \
    --cov-report=term \
    --cov-report=html:tests/htmlcov \
    -vv \
    $EXTRA_PARAMS
