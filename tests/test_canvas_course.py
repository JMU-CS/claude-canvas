"""Offline tests for canvas_course.py: dates, plan display, and ChangeSet's
apply guards. Run: python3 tests/test_canvas_course.py"""
import contextlib, io, json, pathlib, sys, tempfile, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plugins/jmu-canvas/scripts"))
import canvas_course as cc

failures = 0
def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"\n{detail}"))
    failures += not cond

tmp = pathlib.Path(tempfile.mkdtemp())
cc.get_datadir = lambda: tmp

# 1. Date windows.
a = types.SimpleNamespace(unlock_at="2027-03-05T13:00:00Z",   # Fri 8:00 AM EST
                          due_at="2027-03-06T04:59:00Z", lock_at=None)
moved = cc.shift_window(a, days=7)
check("window keeps a blank date blank", moved["lock_at"] == "", moved)
check("window shifts every set date", moved["unlock_at"] == "2027-03-12T13:00:00Z" and moved["due_at"] != a.due_at, moved)
across = cc.shift_window({"due_at": "2027-03-06T04:59:00Z"}, days=14)   # DST starts Mar 14, 2027
check("shift across DST keeps 11:59 PM", cc.show_when(across["due_at"]).endswith("11:59 PM"), across)
check("window() gives blanks as \"\" (which clears on write)", cc.window(a) == {"unlock_at": a.unlock_at, "due_at": a.due_at, "lock_at": ""})
check("same() compares windows by instant",
      cc.same({"due_at": "2027-03-06T04:59:00Z"}, {"due_at": "2027-03-06T04:59:00+00:00"}))
check("same() notices a missing date", not cc.same({"due_at": "2027-03-06T04:59:00Z"}, {}))
shown = cc.show_value(cc.window(a))
check("window shown readably", shown.startswith("unlock Fri 3/5/2027 8:00 AM, due "), shown)

# 2. connect() reads assignments with their Everyone dates.
seen = []
course = types.SimpleNamespace(get_assignment=lambda aid, **kw: seen.append(kw),
                               get_assignments=lambda **kw: seen.append(kw))
cc.everyone_dates(course)
course.get_assignment(5); course.get_assignments(); course.get_assignment(5, override_assignment_dates=True)
check("everyone dates by default", seen[0] == seen[1] == {"override_assignment_dates": False}, seen)
check("caller can still ask for overrides", seen[2] == {"override_assignment_dates": True}, seen)


# 3. ChangeSet: a create plan cannot be applied twice.
def make_plan(title="Add a page", calls=None):
    course = types.SimpleNamespace(id=99, name="Fake")
    plan = cc.ChangeSet(course, title)
    plan.add("page 'X'", "create", None, "unpublished page",
             apply=lambda: calls.append(1))
    return plan


def run(plan, *argv):
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            plan.run(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


calls = []
pid = make_plan(calls=calls).plan_id()
code, out = run(make_plan(calls=calls), "--apply", pid)
check("first apply runs", calls == [1] and code in (0, None), out)
code, out = run(make_plan(calls=calls), "--apply", pid)
check("second apply of the same create plan refused", calls == [1] and code, out)
code, out = run(make_plan(calls=calls))
check("dry run warns it was already made", "already made" in out and "--apply" not in out, out)
check("title is part of the plan id", make_plan("Add a page again", []).plan_id() != pid)
check("a new title does not get past the duplicate guard",
      run(make_plan("Add a page again", calls), "--apply",
          make_plan("Add a page again", []).plan_id())[0] and calls == [1])
check("one journal per apply", len(list(tmp.glob("**/course99_*.json"))) == 1)

# 4. Journals are never overwritten, even within the same second. A plan that
# only modifies keeps its id here because the fake course never changes.
def edit_plan():
    plan = cc.ChangeSet(types.SimpleNamespace(id=98, name="Fake"), "Rename")
    plan.add("module 1 'A'", "name", "A", "B", apply=lambda: None)
    return plan


import datetime as _dt
class Frozen(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 11, 9, 0, 0)
real_datetime = cc.datetime
cc.datetime = types.SimpleNamespace(**{k: getattr(_dt, k) for k in dir(_dt) if not k.startswith("_")})
cc.datetime.datetime = Frozen            # both applies land in the same second
for _ in range(2):
    run(edit_plan(), "--apply", edit_plan().plan_id())
cc.datetime = real_datetime
check("a journal for each apply in the same second", len(list(tmp.glob("**/course98_*.json"))) == 2,
      list(tmp.glob("**/course98_*.json")))

# 5. The guard is per create, so mixing in other changes does not defeat it,
# and repeat_ok lets a deliberate copy through.
def mixed_plan(calls, rename_before):
    plan = cc.ChangeSet(types.SimpleNamespace(id=97, name="Fake"), "Mixed")
    plan.add("module 1 'A'", "name", rename_before, "B", apply=lambda: None)
    plan.add("page 'Y'", "create", None, "unpublished page",
             apply=lambda: calls.append(1) or types.SimpleNamespace(id=555, url="y"))
    return plan

calls = []
run(mixed_plan(calls, "A"), "--apply", mixed_plan([], "A").plan_id())
j = json.loads(sorted(tmp.glob("**/course97_*.json"))[0].read_text())
check("created id journaled", j["changes"][1].get("created", {}).get("id") == 555, j)
code, out = run(mixed_plan(calls, "B"), "--apply", mixed_plan([], "B").plan_id())
check("mixed plan re-run cannot repeat its create", calls == [1] and code and "1. page 'Y'" in out, out)
again = cc.ChangeSet(types.SimpleNamespace(id=97, name="Fake"), "Mixed")
again.add("page 'Y'", "create", None, "unpublished page",
          apply=lambda: calls.append(1), repeat_ok=True)
run(again, "--apply", again.plan_id())
check("repeat_ok allows a deliberate copy", calls == [1, 1])

# 6. Editing the plan script after the dry run changes the id.
orig = cc.script_source
cc.script_source = lambda: b"apply=lambda: page.edit(published=False)"
before_id = make_plan(calls=[]).plan_id()
cc.script_source = lambda: b"apply=lambda: page.edit(published=True)"
check("script source is part of the plan id", make_plan(calls=[]).plan_id() != before_id)
cc.script_source = orig

# 7. Sets give the same id in every run. Set order depends on the process's
# hash seed, so compare ids computed under different seeds.
import os, subprocess
probe = ("import sys, types; sys.path.insert(0, %r); import canvas_course as cc; "
         "cc.script_source = lambda: b''; "
         "p = cc.ChangeSet(types.SimpleNamespace(id=1, name='F'), 't'); "
         "p.add('x', 'tags', {'alpha', 'beta', 'gamma', 'delta'}, {'a'}, apply=None); "
         "print(p.plan_id())") % str(pathlib.Path(cc.__file__).parent)
ids = {subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                      env=dict(os.environ, PYTHONHASHSEED=str(seed))).stdout
       for seed in range(6)}
check("set values give a stable id across runs", len(ids) == 1 and ids != {""}, ids)

# 8. same() tolerates how Canvas hands values back.
check("numbers compare across types", cc.same(3, "3") and cc.same("2.0", 2) and not cc.same(3, "4"))
check("HTML compares by content", cc.same('<p>See <a href="/courses/1/files/2">x</a></p>',
      '<p>See <a href="https://canvas.jmu.edu/courses/1/files/2?verifier=abc">x</a></p>'))
check("HTML text changes are still noticed", not cc.same("<p>old</p>", "<p>new</p>"))
check("booleans compare as booleans", cc.same(True, True) and not cc.same(True, False))

# 9. The apply guards: the plan id, read-back mismatches, stopping on failure,
# what the journal keeps, and skipping changes already in place.
def guard_plan(apply2=lambda: None, check2=lambda: "B"):
    plan = cc.ChangeSet(types.SimpleNamespace(id=96, name="Fake"), "Guards")
    plan.add("module 1 'A'", "name", "A", "B", apply=lambda: None, check=lambda: "B")
    plan.add("module 2 'C'", "name", "C", "B", apply=apply2, check=check2)
    plan.add("module 3 'D'", "name", "D", "B", apply=lambda: None)
    plan.add("module 4 'B'", "name", "B", "B", apply=lambda: None)   # already B
    return plan

def journal96():
    return json.loads(max(tmp.glob("**/course96_*.json"), key=lambda p: p.stat().st_mtime_ns).read_text())

code, out = run(guard_plan(), "--apply", "000000")
check("a wrong plan id applies nothing", code and "different from the one that was approved" in str(code) + out
      and not list(tmp.glob("**/course96_*.json")), out)
check("unchanged entries are skipped", "3 change(s), 1 already as requested" in run(guard_plan())[1])

code, out = run(guard_plan(check2=lambda: "Z"), "--apply", guard_plan().plan_id())
j = journal96()
check("a read-back mismatch is a failure", code and "✗ module 2" in out and j["changes"][1]["status"].startswith("mismatch"), out)
check("the run continues after a mismatch", j["changes"][2]["status"].startswith("ok"), j)

def boom():
    raise RuntimeError("HTTP 500")
code, out = run(guard_plan(apply2=boom), "--apply", guard_plan().plan_id())
j = journal96()
check("an error stops the run", code and len(j["changes"]) == 2 and j["changes"][1]["status"].startswith("error")
      and "1 not attempted" in out, (out, j))
check("the journal keeps before values", [c["before"] for c in j["changes"]] == ["A", "C"], j)

real_stdin = sys.stdin
sys.stdin = io.StringIO("y\n")      # not a terminal
code, out = run(guard_plan(), "--apply")
sys.stdin = real_stdin
check("--apply with no terminal refuses", code and "needs a terminal" in str(code), (code, out))

# 10. Dates from the user: a bare date means 11:59 PM local; "none" clears.
check("bare date is 11:59 PM local", cc.show_when(cc.parse_when("11/16/2026")) == "Mon 11/16/2026 11:59 PM")
check("time of day kept", cc.show_when(cc.parse_when("2027-03-15 08:30")) == "Mon 3/15/2027 8:30 AM")
check("none clears", cc.parse_when("none") == "")

# 11. connect() itself sets Everyone dates (not just the helper).
cfg = tmp / "cfg.ini"
cfg.write_text("[DEFAULT]\nkey = fake\ncourse_id = 5\n")
calls = []
class FakeCanvasAPI:
    def __init__(self, url, key): pass
    def get_course(self, cid):
        return types.SimpleNamespace(id=cid, name="F", workflow_state="unpublished",
                                     get_assignment=lambda aid, **kw: calls.append(kw),
                                     get_assignments=lambda **kw: calls.append(kw))
saved = cc.Canvas, cc.get_config
cc.Canvas, cc.get_config = FakeCanvasAPI, lambda name: cfg
with contextlib.redirect_stderr(io.StringIO()):
    c5 = cc.connect(argv=[])
c5.get_assignments()
cc.Canvas, cc.get_config = saved
check("connect() reads Everyone dates", calls == [{"override_assignment_dates": False}], calls)

# 12. A journal whose create failed does not count as made: retrying it is fine.
err_dir = tmp / "bulk_canvas_actions" / "change_log"
(err_dir / "course95_20261111-090000_abc123.json").write_text(json.dumps({"changes": [
    {"target": "page 'Z'", "field": "create", "before": None, "after": "unpublished page",
     "status": "error: HTTP 500"}]}))
retry = cc.ChangeSet(types.SimpleNamespace(id=95, name="Fake"), "Retry")
retry.add("page 'Z'", "create", None, "unpublished page", apply=lambda: None)
check("a failed create can be retried", retry.already_created() == {})

print("\nfailures:", failures)
sys.exit(failures)
