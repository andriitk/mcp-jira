#!/bin/bash
# Apply lint fixes and formatting in place.

set -euf -o pipefail

ruff check --fix .
ruff format .
