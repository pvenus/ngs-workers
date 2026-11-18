#!/bin/sh
# Explicit source selection, isolated fixtures only. Never starts production daemons.
set -eu
: "${NGS_RUNTIME_SOURCE:?select NGS runtime source for in-memory contracts}"
: "${PDS_RELEASE_SOURCE:?select PDS source for temporary Filex contracts}"
: "${MEDIA_PDS_SOURCE:?select candidate PDS source with media artifacts}"
: "${WORKERS_TEST_PYTHON:?select Python with Pillow and existing test dependencies}"
export NGS_RUNTIME_SOURCE PDS_RELEASE_SOURCE MEDIA_PDS_SOURCE
NGS_NATIVE_SOURCE="$NGS_RUNTIME_SOURCE"
NGS_TEST_PDS_SOURCE="$PDS_RELEASE_SOURCE/data-service"
export NGS_NATIVE_SOURCE NGS_TEST_PDS_SOURCE
cd "$(dirname "$0")/.."
"$WORKERS_TEST_PYTHON" -B -m unittest discover -s tests/resident -v
# Media candidate and existing PDS export the same module names. Isolate their
# fixture imports rather than silently using whichever version was imported first.
for test_file in tests/test*.py; do
    "$WORKERS_TEST_PYTHON" -B -m unittest discover -s tests -p "$(basename "$test_file")" -v
done
