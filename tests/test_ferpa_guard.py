"""What the FERPA hook blocks and allows. Run: python3 tests/test_ferpa_guard.py"""
import json
import pathlib
import subprocess
import sys
import tempfile

HOOK = pathlib.Path(__file__).resolve().parents[1] / "plugins/jmu-canvas/hooks/ferpa_guard.py"
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "bad.py").write_text(
    "from canvas_course import connect\ncourse = connect()\n"
    "for s in course.get_assignment(5).get_submissions(): print(s.score)\n")
(tmp / "good.py").write_text(
    "from canvas_course import connect, ChangeSet\ncourse = connect()\n"
    "for a in course.get_assignments(): print(a.name, a.due_at)\n")
(tmp / "grades.py").write_text(
    "from canvasapi import Canvas\nfor u in Canvas('u','k').get_course(1).get_users(): print(u)\n")

CASES = [
    ("BLOCK", f"python3 {tmp}/bad.py --course-id 1"),
    ("allow", f"python3 {tmp}/good.py --course-id 1"),
    ("BLOCK", "python3 grades.py"),
    ("BLOCK", "python3 - <<'EOF'\nfrom canvasapi import Canvas\n"
              "c=Canvas('https://canvas.jmu.edu','k'); print(list(c.get_course(1).get_enrollments()))\nEOF"),
    ("BLOCK", "curl -H 'Authorization: Bearer x' https://canvas.jmu.edu/api/v1/courses/1/students"),
    ("BLOCK", "python3 -c \"from canvasapi import Canvas; Canvas('u','k').get_course(1, include=['students'])\""),
    ("BLOCK", "curl https://canvas.jmu.edu/api/v1/courses/1/assignments/2/submissions"),
    ("allow", f"python3 {HOOK.parents[1]}/scripts/quiz_tool.py --course-id 1 questions 5"),
    ("allow", f"python3 {HOOK.parents[1]}/scripts/canvas_course.py --course-id 1 inventory modules"),
    ("allow", "grep -n get_submissions grades.py"),
    ("allow", "cat grades.py"),
    ("allow", "curl https://api.github.com/users/octocat"),
    ("allow", "git status"),
]

failures = 0
for expect, command in CASES:
    event = {"tool_name": "Bash", "cwd": str(tmp), "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                       capture_output=True, text=True)
    got = "BLOCK" if r.returncode == 2 else "allow"
    ok = got == expect
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'} [{got}] {command.splitlines()[0][:90]}")
    if not ok:
        print(r.stderr)
print("\nfailures:", failures)
sys.exit(failures)
