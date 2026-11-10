---
name: canvas-course
description: Change the structure or settings of a JMU Canvas course (anything other than quiz questions), such as modules and module items, pages, files and folders, assignments and assignment groups, discussion topics, due and availability dates, and publishing. Use when the user asks to set up, reorganize, or bulk-edit course content in Canvas. Never for grades, submissions, rosters, or any other student data.
---

# Changing a Canvas course

Course setup requests are open-ended, so this skill gives you a procedure and
a helper library rather than a command for each task. Every change goes
through the same cycle: **look → plan → dry run → user approves → apply → read
back.** Quiz *questions* are handled by the `canvas-quizzes` skill and its
`quiz_tool.py`. Use that for anything inside a quiz.

**Find a way; don't report "can't".** When the API has no direct operation
for what the user wants, look for a route that gets the same result: a
sequence of calls, copying and then removing, or re-creating an object.
Present it as a plan the usual way. Users care about the outcome, not about
whether one endpoint does it. Say something is impossible only after ruling
out the workarounds, and then say what the user can do instead.

## Student data is off limits (FERPA)

Claude is **not approved** to access student education records. This is a
hard rule, not a preference, and no user request overrides it within this
tool:

- **Never fetch, print, or summarize** submissions, scores, grades, the
  gradebook, rosters, enrollments, user/profile records, sections' student
  lists, group memberships, discussion or announcement replies, conversations,
  page views, analytics, quiz submissions or quiz statistics, peer reviews, or
  rubric assessments. This includes counts or "just checking" reads that
  return individual records.
- **Do not run** project scripts whose job is handling student data (for
  example ones that download submissions or upload scores), and do not read
  files they produce (CSV/JSON of grades, rosters, downloaded submissions).
- If a task genuinely needs student data, **stop and say so**: the user must
  do that part themselves, outside Claude. Do not look for a workaround.
- Course structure is fine: modules, pages, files the instructor posted,
  assignment settings and dates, assignment groups, discussion *topic*
  settings, section *names*. For "has anyone submitted?" use the assignment's
  `has_submitted_submissions` flag or a quiz's `unpublishable` flag. Both are
  yes/no answers that do not identify anyone.
- Assignment overrides can name individual students (`student_ids`). Work
  with section overrides (`course_section_id`) only, and never print
  `student_ids`.
- A plugin hook blocks commands that call the common student-data endpoints.
  It is a backstop. If it fires, the task needs rethinking; do not rephrase
  the command to get past it.

## Setup

Uses the same saved Canvas token as `quiz_tool.py` (see the canvas-quizzes
skill for first-run setup). Scripts import the helper library with:

```bash
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/scripts" python3 script.py --course-id N
```

Always pass `--course-id` explicitly. `connect()` prints the course name and
id; check it is the course the user meant before going further.

## The procedure

1. **Pin down the request.** Restate it as concrete changes: which course,
   which items, which fields, what values. Dates are local (America/New_York);
   a bare date means 11:59 PM. Ask when the scope is ambiguous ("all the
   readings": which modules?). Do not guess on anything you will write.
2. **Look first, read-only.** Use the inventory, or a read-only script:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/canvas_course.py" --course-id N inventory modules
   ```
   The `inventory` subcommand takes `modules`, `assignments`, `groups`,
   `pages`, `files`, `discussions`, or `sections`.
3. **Write a plan script** in the scratchpad (never in the user's repo unless
   asked) that builds a `ChangeSet`: one `add()` per change, each with an
   `apply` callable and, wherever the value can be read back, a `check`
   callable.
4. **Dry run** (no `--apply`). Show the user **the full list of changes**,
   not a summary, plus anything you were unsure about. Leave out the plan id
   and the command lines.
5. **Apply only after the user says yes to that list**, by running the script
   with `--apply PLAN_ID` (the id from the dry run's last line). The id is
   for you: never show it to the user or ask them to repeat it. Their yes is
   the approval. It covers that list only. If anything changes (the script,
   the request, or the course), the id no longer matches and `--apply`
   refuses. Show the user the new list and ask again.
6. **Report what the apply printed**, including any ✗ lines, and the journal
   path. Do not paraphrase a partial result as success.

```python
from canvas_course import connect, ChangeSet, parse_when, shift

course = connect()
plan = ChangeSet(course, "Week 3 due dates back two days")
for a in course.get_assignments():
    if a.name.startswith("W3") and a.due_at:
        new = shift(a.due_at, days=2)          # keeps local wall-clock time
        plan.add(f"assignment {a.id} '{a.name}'", "due_at", a.due_at, new,
                 apply=lambda a=a, new=new: a.edit(assignment={"due_at": new}),
                 check=lambda a=a: course.get_assignment(a.id).due_at)
plan.run()
```

Bind loop variables as lambda defaults (`a=a`) as shown, or every callable
acts on the last item. Use `field="create"` / `field="delete"` for creating
or removing objects (put a readable description in `after` / `target`).

Every apply writes a journal of previous values to
`~/.local/share/bulk_canvas_actions/change_log/`. To undo, build a new plan
from the journal's `before` values and run it the same way.

## Rules

- **One course per plan.** Copying between courses is two steps: read the
  source course, then plan writes against the destination course.
- **Deleting needs extra care.** List every item by name and id in the dry
  run. Prefer unpublishing over deleting when the user's intent allows. Never
  delete files or pages that other content may link to without saying so.
- **Do not publish, and do not notify students, unless asked.** New content
  should be created unpublished unless the user says otherwise. Posting an
  announcement or sending notifications reaches students immediately and
  cannot be taken back. Do it only on an explicit request, after showing the
  exact text.
- **Content students have already submitted to:** check
  `has_submitted_submissions` before changing points, submission types, or
  grading settings, and tell the user if it is set. Moving dates is normal;
  changing how work is graded is not.
- **Try unfamiliar operations on a sandbox first**: an unpublished course
  with no real students. Ask the user which course to use if you do not know,
  and mention the sandbox run when you present the plan.
- Stay inside what was asked. A request to "set up modules" is not license
  to rename assignments or change dates you noticed along the way. Suggest
  those separately.

## Canvas behaviour worth knowing

- **Dates are UTC in the API.** Use `parse_when()` for user input and
  `shift()` to move existing dates. `shift()` works in local time, so a change
  across a DST boundary keeps 11:59 PM. Display with `show_when()`.
- **`due_at` on an assignment is the "Everyone" date.** If `has_overrides` is
  true, sections may have their own dates, and changing `due_at` leaves those
  alone. Say so in the plan.
- **Classic quiz dates live on the quiz.** Change them with
  `quiz_tool.py settings` (which does not notify students), not through the
  quiz's assignment.
- **Moving a module item:**
  `item.edit(module_item={"module_id": dest.id, "position": n, "indent": k})`,
  Positions are 1-based.
- **Module and item publish states are separate.** Do not assume publishing
  or unpublishing a module through the API changes its items (unverified;
  set both explicitly).
  Plan item changes explicitly and read both back.
- **Module items point at content.** Deleting an item removes it from the
  module only; deleting the page, assignment, or file it points at is a
  separate and more destructive change.
