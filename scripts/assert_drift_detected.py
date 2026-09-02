#!/usr/bin/env python3
"""Assert that Drift actually detected the drifted server's divergences.

`make test_drift` runs a server that deliberately diverges from notes.mcp.yaml,
so `drift verify` is expected to exit non-zero. But a non-zero exit on its own
proves nothing — Drift exits non-zero when it can't authenticate, can't reach
the server, or can't find the testcase file too. Treating any failure as
"drift detected" would make the CI job pass while testing nothing.

So this reads the result bundle Drift wrote (`--generate-result`) and checks it
recorded failures for the specific operations we expect.

Usage: assert_drift_detected.py <drift-verify-output-dir>
"""
import json
import sys
from pathlib import Path

# Operations that must be recorded as failing. `drift_all` catches the tool
# surface moving; `createNote_Success` catches the outputSchema violation.
EXPECTED_FAILURES = {"drift_all", "createNote_Success"}

# The result bundle is a multipart document; the operation results live in a
# JSON part that starts with this key.
JSON_MARKER = '{"namespaceMap'


def load_results(bundle: Path) -> dict:
    """Extracts the JSON payload embedded in a Drift result bundle."""
    text = bundle.read_text()
    start = text.find(JSON_MARKER)
    if start == -1:
        raise ValueError(f"no JSON payload found in {bundle}")
    # The payload is followed by further multipart sections, so decode just the
    # first complete JSON value rather than the rest of the file.
    return json.JSONDecoder().raw_decode(text[start:])[0]


def operation_results(bundle: Path) -> dict[str, str]:
    """Maps operation name -> result string (e.g. "OK", "Error")."""
    return {
        op["operation"]: op.get("result", "")
        for suite in load_results(bundle).get("operations", {}).values()
        for op in suite
    }


def main() -> int:
    output_dir = Path(sys.argv[1])
    bundles = sorted((output_dir / "results").glob("verification.*.result"))

    if not bundles:
        print(f"FAIL: Drift wrote no result bundle under {output_dir}/results.")
        print("      It exited before running the tests — check the log above for")
        print("      an auth, connection or configuration error. This does NOT mean")
        print("      the drifted server went undetected; it means nothing was tested.")
        return 1

    results = operation_results(bundles[-1])
    still_passing = {op for op in EXPECTED_FAILURES if results.get(op) == "OK"}
    absent = EXPECTED_FAILURES - results.keys()

    if absent:
        print(f"FAIL: the result bundle has no entry for {', '.join(sorted(absent))}.")
        print(f"      Operations it does record: {', '.join(sorted(results)) or '(none)'}")
        print("      Either those operations were renamed in the testcase file, or")
        print("      the run stopped before reaching them.")
        return 1

    if still_passing:
        print(f"FAIL: Drift ran but {', '.join(sorted(still_passing))} still passed.")
        print("      The drifted server diverges from notes.mcp.yaml, so these")
        print("      should have failed. Either detection regressed, or --drift")
        print("      mode in server/server.py no longer diverges.")
        return 1

    detail = ", ".join(f"{op}={results[op]}" for op in sorted(EXPECTED_FAILURES))
    print(f"PASS: Drift detected the divergence from notes.mcp.yaml — {detail}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
