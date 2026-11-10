"""
Safe changes to a Canvas course: a plan/review/apply/verify cycle, plus a
read-only inventory.

Course setup requests are open-ended (move files, build modules, shift due
dates, ...), so there is no subcommand per task. Instead, a short script per
request builds a ChangeSet. Run without --apply it only prints the plan and a
plan id; run with --apply PLAN_ID it applies exactly that plan, reads each
value back, and journals the old values so the change can be undone.

    from canvas_course import connect, ChangeSet, parse_when

    course = connect()                   # --course-id N from the command line
    plan = ChangeSet(course, "Push Week 3 due dates back two days")
    for a in course.get_assignments():
        if "Week 3" in a.name and a.due_at:
            new = shift(a.due_at, days=2)
            plan.add(f"assignment {a.id} '{a.name}'", "due_at", a.due_at, new,
                     apply=lambda a=a, new=new: a.edit(assignment={"due_at": new}),
                     check=lambda a=a: course.get_assignment(a.id).due_at)
    plan.run()

    python3 week3.py --course-id N                 # dry run: prints plan + id
    python3 week3.py --course-id N --apply 3f2a9c  # after the user approves

Read-only inventory, no script needed:

    python3 canvas_course.py --course-id N inventory modules
    python3 canvas_course.py --course-id N inventory assignments

FERPA: nothing here reads student data, and scripts built on it must not
either. See the canvas-course skill.
"""

import argparse
import configparser
import datetime
import hashlib
import json
import re
import sys
import zoneinfo

from canvasapi import Canvas
from canvas_config import get_config, get_datadir

CANVAS_URL = "https://canvas.jmu.edu"
# Dates are read and displayed in this zone; Canvas stores UTC.
LOCAL_TZ = zoneinfo.ZoneInfo("America/New_York")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})$")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def parse_when(text):
    """Parse a local date/time into a UTC ISO string Canvas accepts.

    Accepts 'M/D/YYYY', 'M/D/YYYY HH:MM', 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM'.
    A bare date means 11:59 PM, which is what a due date almost always means.
    'none' or 'clear' returns "" (unset the field).
    """
    if text is None:
        return None
    if text.strip().lower() in ("none", "clear", ""):
        return ""
    for fmt, bare in (("%m/%d/%Y %H:%M", False), ("%Y-%m-%d %H:%M", False),
                      ("%m/%d/%Y", True), ("%Y-%m-%d", True)):
        try:
            dt = datetime.datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        if bare:
            dt = dt.replace(hour=23, minute=59)
        return to_canvas(dt.replace(tzinfo=LOCAL_TZ))
    raise argparse.ArgumentTypeError(
        f"could not parse date '{text}'. Use M/D/YYYY, M/D/YYYY HH:MM "
        "(24-hour), or 'none' to clear it.")


def from_canvas(iso):
    """A Canvas UTC timestamp as an aware datetime, or None."""
    if not iso:
        return None
    return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def to_canvas(dt):
    """An aware datetime as the UTC ISO string Canvas returns."""
    return (dt.astimezone(datetime.timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def shift(iso, days=0, hours=0, minutes=0):
    """Move a Canvas timestamp by local calendar days, keeping wall-clock time.

    Shifting in local time matters across a DST change: "two days later"
    should keep 11:59 PM, not drift to 10:59 PM.
    """
    local = from_canvas(iso).astimezone(LOCAL_TZ).replace(tzinfo=None)
    moved = local + datetime.timedelta(days=days, hours=hours, minutes=minutes)
    return to_canvas(moved.replace(tzinfo=LOCAL_TZ))


def show_when(iso):
    """Render a Canvas UTC timestamp in local time."""
    if not iso:
        return "-"
    try:
        return from_canvas(iso).astimezone(LOCAL_TZ).strftime("%a %-m/%-d/%Y %-I:%M %p")
    except Exception:
        return str(iso)


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------

def connect(course_id=None, argv=None):
    """The Course to work on, from --course-id or the saved default.

    Never writes the config: a one-off --course-id must not change the
    default other scripts use. Prints the course name so the target is
    always visible in the output.
    """
    if course_id is None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--course-id")
        known, _ = parser.parse_known_args(argv)
        course_id = known.course_id
    config = configparser.ConfigParser()
    config.read(get_config("config.ini"))
    defaults = config["DEFAULT"]
    if "key" not in defaults:
        sys.exit("No Canvas key saved. Run quiz_tool.py once with --key-file.")
    course_id = course_id or defaults.get("course_id")
    if not course_id:
        sys.exit("No --course-id given and no default saved.")
    course = Canvas(CANVAS_URL, defaults["key"]).get_course(course_id)
    print(f"Course: {course.name} (id {course.id}, "
          f"{getattr(course, 'workflow_state', '?')})", file=sys.stderr)
    return course


# ---------------------------------------------------------------------------
# Plan / apply / verify
# ---------------------------------------------------------------------------

def show_value(value):
    if isinstance(value, str) and ISO_RE.match(value):
        return show_when(value)
    if value is None or value == "":
        return "-"
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= 70 else text[:67] + "..."


def same(a, b):
    """Equal as Canvas values: timestamps by instant, None == ""."""
    if a in (None, "") and b in (None, ""):
        return True
    if isinstance(a, str) and isinstance(b, str) and ISO_RE.match(a) and ISO_RE.match(b):
        return from_canvas(a) == from_canvas(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-9
    return a == b


class ChangeSet:
    """A reviewed list of changes, applied only with the matching plan id."""

    def __init__(self, course, title):
        self.course = course
        self.title = title
        self.changes = []
        self.unchanged = 0

    def add(self, target, field, before, after, apply, check=None):
        """Record one change.

        target  -- human-readable object, include its id: "page 'syllabus'"
        field   -- what changes ("due_at", "position", "create", "delete", ...)
        before  -- current value (None for a create)
        after   -- new value (None for a delete)
        apply   -- zero-argument callable that performs the change
        check   -- optional zero-argument callable returning the live value
                   after applying; compared with `after`. Supply it whenever
                   the value can be read back.
        """
        if field not in ("create", "delete") and same(before, after):
            self.unchanged += 1
            return
        self.changes.append(dict(target=target, field=field, before=before,
                                 after=after, apply=apply, check=check))

    def plan_id(self):
        blob = json.dumps([[c["target"], c["field"], c["before"], c["after"]]
                           for c in self.changes] + [self.course.id],
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:6]

    def show(self):
        print(f"\n{self.title}")
        print(f"Course: {self.course.name} (id {self.course.id})")
        print(f"{len(self.changes)} change(s)"
              + (f", {self.unchanged} already as requested" if self.unchanged else ""))
        for i, c in enumerate(self.changes, 1):
            if c["field"] == "create":
                print(f"  {i:>3}. CREATE {c['target']}: {show_value(c['after'])}")
            elif c["field"] == "delete":
                print(f"  {i:>3}. DELETE {c['target']}")
            else:
                print(f"  {i:>3}. {c['target']}: {c['field']}: "
                      f"{show_value(c['before'])} -> {show_value(c['after'])}")

    def run(self, argv=None):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--apply", metavar="PLAN_ID")
        known, _ = parser.parse_known_args(argv)

        self.show()
        pid = self.plan_id()
        if not self.changes:
            print("\nNothing to change.")
            return
        if known.apply is None:
            print(f"\n*** DRY RUN: nothing was changed. Plan id {pid}. ***")
            print(f"After the user approves this list, re-run with --apply {pid}")
            return
        if known.apply != pid:
            sys.exit(f"\nPlan id {known.apply} does not match the current plan "
                     f"({pid}): the course or the script changed since the "
                     "dry run. Nothing was changed. Show the user the new list.")

        journal_dir = get_datadir() / "bulk_canvas_actions" / "change_log"
        journal_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        journal = journal_dir / f"course{self.course.id}_{stamp}_{pid}.json"
        log = {"title": self.title, "course_id": self.course.id, "plan_id": pid,
               "changes": []}

        def save():
            journal.write_text(json.dumps(log, indent=2, default=str))

        print(f"\nApplying plan {pid} (journal: {journal})")
        failed = 0
        for i, c in enumerate(self.changes, 1):
            entry = {k: c[k] for k in ("target", "field", "before", "after")}
            log["changes"].append(entry)
            try:
                c["apply"]()
            except Exception as e:
                entry["status"] = f"error: {e}"
                save()
                print(f"  {i:>3}. ✗ {c['target']}: {e}")
                print(f"\nStopped at change {i}; {len(self.changes) - i} not "
                      "attempted. Earlier changes were applied (see journal).")
                sys.exit(1)
            if c["check"] is not None:
                try:
                    got = c["check"]()
                except Exception as e:
                    got = f"<could not read back: {e}>"
                if not same(got, c["after"]):
                    entry["status"] = f"mismatch: Canvas has {got!r}"
                    failed += 1
                    print(f"  {i:>3}. ✗ {c['target']}: sent {show_value(c['after'])}, "
                          f"Canvas has {show_value(got)}")
                    save()
                    continue
                entry["status"] = "ok (read back)"
                print(f"  {i:>3}. ✓ {c['target']} (read back)")
            else:
                entry["status"] = "ok (not read back)"
                print(f"  {i:>3}. ✓ {c['target']} (not read back)")
            save()
        print(f"\n{len(self.changes) - failed}/{len(self.changes)} applied. "
              f"Previous values are in {journal}")
        if failed:
            sys.exit(f"{failed} change(s) did not stick; check them in Canvas.")


# ---------------------------------------------------------------------------
# Read-only inventory (course structure only -- never student data)
# ---------------------------------------------------------------------------

def inv_modules(course):
    for m in course.get_modules():
        print(f"[module {m.id}] pos {m.position}  "
              f"{'published' if getattr(m, 'published', False) else 'UNPUBLISHED'}  "
              f"{m.name}" + (f"  unlocks {show_when(m.unlock_at)}"
                             if getattr(m, "unlock_at", None) else ""))
        for it in m.get_module_items():
            ref = getattr(it, "page_url", None) or getattr(it, "content_id", "")
            pub = "" if getattr(it, "published", True) else "  (unpublished)"
            print(f"    {'  ' * getattr(it, 'indent', 0)}[item {it.id}] "
                  f"{it.type:<16} {it.title}  -> {ref}{pub}")


def inv_assignments(course):
    groups = {g.id: g.name for g in course.get_assignment_groups()}
    print(f"{'id':>10}  {'pub':3}  {'pts':>5}  {'due':24} {'unlock':24} "
          f"{'lock':24} group / name")
    for a in course.get_assignments(order_by="due_at"):
        print(f"{a.id:>10}  {'Y' if a.published else 'N':3}  "
              f"{str(a.points_possible):>5}  {show_when(a.due_at):24} "
              f"{show_when(a.unlock_at):24} {show_when(a.lock_at):24} "
              f"{groups.get(a.assignment_group_id, '?')} / {a.name}")


def inv_groups(course):
    for g in course.get_assignment_groups():
        print(f"{g.id:>10}  pos {g.position}  weight {g.group_weight}  {g.name}")


def inv_pages(course):
    for p in course.get_pages():
        print(f"{'Y' if p.published else 'N'}  "
              f"{'FRONT ' if getattr(p, 'front_page', False) else ''}"
              f"{p.url:40} {p.title}")


def inv_files(course):
    folders = {f.id: f.full_name for f in course.get_folders()}
    for f in course.get_files():
        flags = " ".join(x for x, on in (("locked", f.locked), ("hidden", f.hidden))
                         if on)
        print(f"{f.id:>10}  {f.size:>10,}  {folders.get(f.folder_id, '?')}/"
              f"{f.display_name}  {flags}")


def inv_discussions(course):
    for t in course.get_discussion_topics():
        print(f"{t.id:>10}  {'Y' if t.published else 'N'}  "
              f"post {show_when(getattr(t, 'delayed_post_at', None)):24} "
              f"lock {show_when(getattr(t, 'lock_at', None)):24} {t.title}")


def inv_sections(course):
    for s in course.get_sections():
        print(f"{s.id:>10}  {s.name}")


INVENTORY = {"modules": inv_modules, "assignments": inv_assignments,
             "groups": inv_groups, "pages": inv_pages, "files": inv_files,
             "discussions": inv_discussions, "sections": inv_sections}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--course-id")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inventory", help="read-only listing of course structure")
    p.add_argument("what", choices=sorted(INVENTORY))
    args = parser.parse_args()
    course = connect(args.course_id)
    INVENTORY[args.what](course)


if __name__ == "__main__":
    main()
