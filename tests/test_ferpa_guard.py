"""What the FERPA hook blocks and allows. Run: python3 tests/test_ferpa_guard.py

The cases are in ferpa_cases.json. Keep them out of this file: the hook scans
any .py file a command runs, so fixtures written here would block the test.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
HOOK = HERE.parent / "plugins/jmu-canvas/hooks/ferpa_guard.py"
SCRIPTS = HOOK.parents[1] / "scripts"
fixtures = json.loads((HERE / "ferpa_cases.json").read_text())

tmp = pathlib.Path(tempfile.mkdtemp())
for name, text in fixtures["files"].items():
    (tmp / name).write_text(text)
os.chmod(tmp / "fetch", 0o755)


def fill(text):
    return (text.replace("{tmp}", str(tmp)).replace("{scripts}", str(SCRIPTS))
            .replace("{hook}", str(HOOK)))


failures = 0
for expect, case in fixtures["cases"]:
    if isinstance(case, str):
        event = {"tool_name": "Bash", "cwd": str(tmp),
                 "tool_input": {"command": fill(case)}}
        shown = fill(case).splitlines()[0]
    else:
        tool_input = case.get("input") or {"url": case["url"]}
        event = {"tool_name": case["tool"], "cwd": str(tmp), "tool_input": tool_input}
        shown = f"{case['tool']} {json.dumps(tool_input)}"
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                       capture_output=True, text=True)
    # Exit 2 blocks; 0 allows. Anything else is a crash, which Claude Code
    # treats as allow, so it must never count as a pass.
    got = {2: "BLOCK", 0: "allow"}.get(r.returncode, f"CRASH({r.returncode})")
    ok = got == expect
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'} [{got}] {shown[:90]}")
    if not ok:
        print(r.stderr)
print("\nfailures:", failures)
sys.exit(failures)
