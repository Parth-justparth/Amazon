# Run the full test suite (unit + property tests) with STUB_MODE enabled.
# Usage: ./scripts/run_tests.ps1
$env:SECONDLIFE_STUB_MODE = "true"
$env:HYPOTHESIS_PROFILE = "ci"
python -m pytest
