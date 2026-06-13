#!/usr/bin/env bash
# Run the full test suite (unit + property tests) with STUB_MODE enabled.
# Usage: ./scripts/run_tests.sh
set -euo pipefail
export SECONDLIFE_STUB_MODE=true
export HYPOTHESIS_PROFILE=ci
python -m pytest "$@"
