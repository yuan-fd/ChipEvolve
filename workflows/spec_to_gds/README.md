# Spec to GDS workflow

This workflow is intentionally not marked implemented. Its acceptance chain is:

structured specification -> RTL candidate -> lint/compile -> executable tests
and reference model -> simulation/formal checks -> user acceptance -> ORFS run.

The generator in `~/iccad/generate_and_analyze.py` remains a baseline candidate,
but synthesis success is not functional verification. It will be adapted only
after the validation request/result contracts exist.

