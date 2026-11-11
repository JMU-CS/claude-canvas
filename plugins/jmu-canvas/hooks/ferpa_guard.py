#!/usr/bin/env python3
"""PreToolUse hook: keep Canvas student data out of Claude's context.

Claude is not approved under FERPA to see student education records. This
blocks tool calls that would fetch submissions, grades, rosters, enrollments,
user records, analytics, quiz statistics, page views, messages, or discussion
posts from Canvas:

- Bash commands, and the script files they run (plus local modules those
  scripts import, one level deep). Only commands that run something (an
  interpreter, curl, ./script, ...) and involve Canvas are checked, so code
  search and unrelated work pass through.
- WebFetch and browser navigation to Canvas pages that show student data
  (gradebook, SpeedGrader, People, quiz statistics, discussion threads, ...).

It is a backstop for the written rules in the skills, not a guarantee. It
reads text, not behaviour: code that builds an endpoint name at run time, a
student-data call more than one import away, clicks inside a page the browser
is already on, and files read with other tools are not caught.

Exit 2 blocks the tool call and shows stderr to Claude.
"""
import json
import os
import pathlib
import re
import sys
import urllib.parse

# The command runs a program that could reach the network: an interpreter or
# downloader by name, also when called by path (/usr/bin/python3,
# .venv/bin/python), a runner (uv run, poetry run, ...), or ./something.
RUNS = re.compile(r"(?:^|[\s;&|(`/])(?:python[\d.]*|ipython|jupyter|curl|wget|http"
                  r"|node|deno|bun|npx|ruby|perl|php|bash|sh|zsh|uv|uvx|poetry"
                  r"|pipenv|conda|env)\b|(?:^|[\s;&|(`])\./")
# The code involves Canvas at all.
CANVAS = re.compile(r"canvas|instructure|quiz_tool", re.I)

# A REST path segment counts only inside something that looks like a Canvas
# API path (courses/1/..., /api/v1/..., /api/graphql), and only as a whole
# segment, so page slugs like /pages/submissions-policy and quiz text about
# REST ("GET /users/42") pass.
API_PATH = (r"(?:api/v1/|\bcourses/|\bsections/|\bquizzes/|\bassignments/"
            r"|\bdiscussion_topics/|\busers/)[^\s'\"]*?")
SEGMENT_END = r"(?=[/?#'\"\s)]|$)"

STUDENT_DATA = [
    # canvasapi method names
    (r"\bget_(?:multiple_)?submissions?\b", "submissions"),
    (r"\bget_\w*submissions?\w*\b", "submissions"),
    (r"\bsubmissions_bulk_update\b", "grades"),
    (r"\bget_users?(?:_\w+)?\b", "user records"),
    (r"\bget_enrollments?\b", "enrollments"),
    (r"\bget_\w*students?\w*\b", "student lists"),
    (r"\bget_\w*grade(?:book)?_?\w*\b", "grades"),
    (r"\bget_\w*participation\w*\b", "analytics"),
    (r"\bget_(?:course|user)_level_\w+\b", "analytics"),
    (r"\bget_user_in_a_course_level_\w+\b", "analytics"),
    (r"\bget_page_views\b", "page views"),
    (r"\bget_conversations?\b", "messages"),
    (r"\b(?:get|list)_(?:topic_)?entries\b", "discussion posts"),
    (r"\bget_entries\b|\bget_replies\b|\bget_topic_view\b", "discussion posts"),
    (r"\bget_peer_reviews\b", "peer reviews"),
    (r"\bget_memberships\b|\bget_group_members\w*\b", "group membership"),
    (r"\bget_outcome_result\w*", "outcome results"),
    (r"\bget_authentication_events\b", "login records"),
    (r"\bget_(?:quiz_)?statistics\b|\bget_all_quiz_reports\b|\bget_quiz_report\b"
     r"|\bcreate_report\b", "quiz statistics / reports"),
    (r"\bget_rubric_assessments?\b|\brubric_assessments?\b", "rubric assessments"),
    # raw REST paths (canvasapi requester paths, curl, requests, ...)
    (API_PATH + r"/(?:submissions|quiz_submissions|submission_history|enrollments"
     r"|students|gradeable_students|recent_students|users|search_users"
     r"|gradebook_history|grade_change|page_views|analytics|conversations"
     r"|peer_reviews|entries|entry_list|participants|memberships|statistics"
     r"|reports|outcome_results|outcome_rollups|rubric_assessments)"
     + SEGMENT_END, "a student-data endpoint"),
    (API_PATH + r"/discussion_topics/\d+/view" + SEGMENT_END, "discussion posts"),
    (r"\bapi/graphql\b|\b(?:submissions|enrollments|users|students)Connection\b",
     "GraphQL student data"),
    # include[] options that attach student data to otherwise safe objects,
    # in code form: include=['students'], include[]=students, "include[]": [...]
    (r"include(?:\[\])?['\"]?\s*[:=]\s*\[?\s*['\"]?(?:students|enrollments"
     r"|submission_history|all_submissions|observed_users|test_student|user"
     r"|assessments|submission)\b", "an include of student data"),
]
PATTERNS = [(re.compile(p), label) for p, label in STUDENT_DATA]

# Files a command may run: scripts by extension, or ./name.
SCRIPT_FILE = re.compile(r"[\w./~${}@+-]+\.(?:py|sh|js|mjs|cjs|ts|rb|pl|php|ipynb)\b"
                         r"|(?<![\w/])\./[\w.@+-]+")
MODULE_FLAG = re.compile(r"\s-m\s+([\w.]+)")
IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)

# Canvas pages (browser / WebFetch) that show student data.
STUDENT_PAGES = re.compile(
    r"/(?:gradebook|grades|speed_grader|users|people|enrollments|submissions"
    r"|quiz_submissions|statistics|analytics|conversations|inbox|page_views"
    r"|groups|rubric_assessments|outcomes/users)" + SEGMENT_END
    + r"|/discussion_topics/\d+(?:/|$)|/quizzes/\d+/history" + SEGMENT_END)


def read_text(path):
    try:
        if path.is_file() and path.stat().st_size < 2_000_000:
            return path.read_text(errors="replace")
    except OSError:
        pass
    return None


def files_run(command, cwd):
    """(name, text) for each script the command names, the module a
    `python -m` runs, and local modules those import (one level)."""
    base = pathlib.Path(cwd or ".")
    # This file names every pattern it looks for; scanning it would block
    # running or debugging the hook itself. Getting code past the guard this
    # way would mean editing the guard, so this exemption adds no real hole.
    seen = {pathlib.Path(__file__).resolve(): None}

    def add(path):
        path = pathlib.Path(os.path.expandvars(os.path.expanduser(str(path))))
        if not path.is_absolute():
            path = base / path
        if path.resolve() in seen or path in seen:
            return None
        text = read_text(path)
        if text is not None:
            seen[path] = text
        return text

    for token in SCRIPT_FILE.findall(command):
        add(token)
    for module in MODULE_FLAG.findall(command):
        rel = pathlib.Path(*module.split("."))
        add(rel.with_suffix(".py")) or add(rel / "__init__.py")
    for path, text in list(seen.items()):
        if text is None or path.suffix != ".py":
            continue
        for a, b in IMPORT.findall(text):
            rel = pathlib.Path(*(a or b).split("."))
            for folder in (path.parent, base):
                add(folder / rel.with_suffix(".py")) or add(folder / rel / "__init__.py")
    return [(str(p), t) for p, t in seen.items() if t is not None]


def find_hits(text):
    hits = []
    for pattern, label in PATTERNS:
        m = pattern.search(text)
        if m:
            hits.append((m.group(0), label))
    return hits


def block(found):
    print("Blocked by the jmu-canvas FERPA guard. Claude is not approved to "
          "access student education records, so it must not fetch Canvas "
          "submissions, grades, rosters, enrollments, user records, analytics, "
          "quiz statistics, messages, or discussion posts.\n"
          + "\n".join(dict.fromkeys(found)) +
          "\nDo the task without student data. If it truly needs that data, "
          "stop and tell the user to run it themselves outside Claude; do not "
          "rephrase the command to get around this check.", file=sys.stderr)
    return 2


def check_bash(command, cwd):
    if not RUNS.search(command):
        return 0
    sources = [("the command", command)] + files_run(command, cwd)
    involves_canvas = any(CANVAS.search(text) for _, text in sources)
    if not involves_canvas:
        return 0
    found = [f"  {label}: `{match}` in {where}"
             for where, text in sources for match, label in find_hits(text)]
    return block(found) if found else 0


def check_url(url):
    parsed = urllib.parse.urlparse(url or "")
    if not CANVAS.search(parsed.netloc):
        return 0
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    m = STUDENT_PAGES.search(path)
    if m or find_hits(path):
        return block([f"  a Canvas page with student data: {url}"])
    return 0


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    try:
        if tool == "Bash":
            return check_bash(tool_input.get("command") or "", event.get("cwd"))
        if tool == "WebFetch" or tool.endswith("__navigate"):
            return check_url(tool_input.get("url"))
    except Exception as e:
        # Claude Code treats a crashed hook as "allow". Fail closed instead
        # when Canvas is involved, so a bug here cannot open the gate.
        if CANVAS.search(json.dumps(tool_input)):
            print(f"The jmu-canvas FERPA guard failed ({type(e).__name__}: {e}), "
                  "so this Canvas command is blocked. Tell the user; the hook "
                  "needs fixing.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
