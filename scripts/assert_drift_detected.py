#!/usr/bin/env python3
"""Assert that Drift actually detected the drifted server's divergences.

`make test_drift` runs a server that deliberately diverges from notes.mcp.yaml,
so `drift verify` is expected to exit non-zero. But a non-zero exit on its own
proves nothing — Drift exits non-zero when it can't authenticate, can't reach
the server, or can't find the testcase file too. Treating any failure as
"drift detected" would make the CI job pass while testing nothing.

So this checks that Drift *ran to completion and reported the specific findings
we expect*, by reading the JUnit report it produced.

Usage: assert_drift_detected.py <drift-verify-output-dir>
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Operations that must be reported as failing. `drift_all` catches the tool
# surface moving; `createNote_Success` catches the outputSchema violation.
EXPECTED_FAILURES = {"drift_all", "createNote_Success"}


def failed_operations(report: Path) -> set[str]:
    """Returns identifiers for the testcases the JUnit report records as failed.

    The junit-output plugin writes the operation id to `classname` and the
    operation's description to `name`, so both are collected and either may
    match an expected id.
    """
    root = ET.parse(report).getroot()
    failed = set()
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed.update(v for v in (case.get("classname"), case.get("name")) if v)
    return failed


def main() -> int:
    output_dir = Path(sys.argv[1])
    report = output_dir / "reports" / "junit" / "verification-result.xml"

    if not report.is_file():
        print(f"FAIL: Drift produced no JUnit report at {report}.")
        print("      It exited before running the tests — check the log above for")
        print("      an auth, connection or configuration error. This does NOT mean")
        print("      the drifted server went undetected; it means nothing was tested.")
        return 1

    failed = failed_operations(report)
    missing = EXPECTED_FAILURES - failed

    if missing:
        print(f"FAIL: Drift ran but did not flag {', '.join(sorted(missing))}.")
        print(f"      Operations it did flag: {', '.join(sorted(failed)) or '(none)'}")
        print("      The drifted server diverges from notes.mcp.yaml, so these")
        print("      should have failed. Either detection regressed, or the")
        print("      --drift mode in server/server.py no longer diverges.")
        return 1

    print(f"PASS: Drift detected the divergence from notes.mcp.yaml — "
          f"flagged {', '.join(sorted(EXPECTED_FAILURES))}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
