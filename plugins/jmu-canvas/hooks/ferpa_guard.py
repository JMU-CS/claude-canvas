#!/usr/bin/env python3
"""PreToolUse hook: keep Canvas student data out of Claude's context.

Claude is not approved under FERPA to see student education records. This
blocks Bash commands -- and the script files they run -- that would fetch
submissions, grades, rosters, enrollments, user records, analytics, page
views, messages, or discussion posts from Canvas.

It is a backstop for the written rules in the skills, not a guarantee: it
reads the command text and any .py/.sh file named in it, nothing deeper. It
only looks at commands that run something (python, curl, ...) and only when
Canvas is involved, so ordinary code search and unrelated work pass through.

Exit 2 blocks the tool call and shows stderr to Claude.
"""
import json
import os
import pathlib
import re
import sys

# The command runs a program that could reach the network.
RUNS = re.compile(r"(?:^|[\s;&|(`])(?:python[\d.]*|curl|wget|http|node|ruby|perl"
                  r"|bash|sh|zsh)\b")
# The code involves Canvas at all.
CANVAS = re.compile(r"canvas|instructure|quiz_tool", re.I)

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
    (r"\bget_entries\b|\bget_replies\b", "discussion posts"),
    (r"\bget_peer_reviews\b", "peer reviews"),
    (r"\bget_memberships\b|\bget_group_members\w*\b", "group membership"),
    (r"\bget_outcome_results?\b", "outcome results"),
    (r"\bget_authentication_events\b", "login records"),
    # raw REST paths
    (r"/(?:submissions|quiz_submissions|submission_history|enrollments|students"
     r"|recent_students|users|search_users|gradebook_history|grade_change"
     r"|page_views|analytics|conversations|peer_reviews|entries|entry_list"
     r"|participants|memberships)\b", "a student-data endpoint"),
    # include[] options that attach student data to otherwise safe objects
    (r"include.{0,40}?[\"'\[=](?:students|enrollments|submission_history"
     r"|all_submissions|observed_users|test_student|user)\b", "an include of student data"),
]
PATTERNS = [(re.compile(p), label) for p, label in STUDENT_DATA]

# Scripts in a project that exist to handle student data. Running them would
# print it into the conversation.
SCRIPT_FILE = re.compile(r"[\w./~${}@+-]+\.(?:py|sh)\b")


def scripts_named(command, cwd):
    for token in SCRIPT_FILE.findall(command):
        path = pathlib.Path(os.path.expandvars(os.path.expanduser(token)))
        if not path.is_absolute():
            path = pathlib.Path(cwd or ".") / path
        try:
            if path.is_file() and path.stat().st_size < 2_000_000:
                yield str(path), path.read_text(errors="replace")
        except OSError:
            continue


def find_hits(text):
    hits = []
    for pattern, label in PATTERNS:
        m = pattern.search(text)
        if m:
            hits.append((m.group(0), label))
    return hits


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    if not RUNS.search(command):
        return 0

    sources = [("the command", command)]
    sources.extend(scripts_named(command, event.get("cwd")))

    found = []
    for where, text in sources:
        if not (CANVAS.search(text) or CANVAS.search(command)):
            continue
        for match, label in find_hits(text):
            found.append(f"  {label}: `{match}` in {where}")
    if not found:
        return 0

    print("Blocked by the jmu-canvas FERPA guard. Claude is not approved to "
          "access student education records, so it must not fetch Canvas "
          "submissions, grades, rosters, enrollments, user records, analytics, "
          "messages, or discussion posts.\n" + "\n".join(dict.fromkeys(found)) +
          "\nDo the task without student data. If it truly needs that data, "
          "stop and tell the user to run it themselves outside Claude; do not "
          "rephrase the command to get around this check.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
