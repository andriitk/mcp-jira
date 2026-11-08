#!/bin/bash
# Lint, format check and tests: the same gate locally, in Docker and in CI.

set -euf -o pipefail

echo "running ruff check..."
ruff check .

echo "running ruff format --check..."
ruff format --check .

echo "running tests..."
exec pytest -p no:cacheprovider
