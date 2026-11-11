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
out the workarounds, and then say what the user can do instead. Deleting and
re-creating something students have used (an assignment, quiz, or discussion
with submissions or replies) is not a workaround: it loses that work, its
grades and its links. Say exactly what would be lost before offering it.

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
- **No browser or web tools on Canvas pages that show students:** the
  gradebook, SpeedGrader, People, quiz statistics or moderation, discussion
  threads, Inbox, or any student's page. The instructor's browser is logged in
  as them, so these pages carry the same records the API does. Use the API for
  course structure instead.
- A plugin hook blocks commands that call the common student-data endpoints,
  and web fetches or browser navigation to those pages. It is a backstop. If
  it fires, the task needs rethinking; do not rephrase the command to get past
  it. If it fires on something that plainly touches no student data, tell the
  user what it matched rather than working around it.

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
from canvas_course import connect, ChangeSet, shift_window, window

course = connect()
plan = ChangeSet(course, "Week 3 due dates back two days")
for a in course.get_assignments():
    if a.name.startswith("W3") and a.due_at:
        new = shift_window(a, days=2)   # unlock, due and lock together
        plan.add(f"assignment {a.id} '{a.name}'", "dates", window(a), new,
                 apply=lambda a=a, new=new: a.edit(assignment=new),
                 check=lambda a=a: window(course.get_assignment(a.id)))
plan.run()
```

Bind loop variables as lambda defaults (`a=a`) as shown, or every callable
acts on the last item. Use `field="create"` / `field="delete"` for creating
or removing objects:

- **Create:** put a readable description in `after`, including whether the
  object will be published. Make `apply` *return* the new object
  (`apply=lambda: course.create_page(...)`). The journal then records its id,
  which is what makes the create undoable. A create that an earlier apply
  already made (same target and `after`) is refused, so a retried or
  re-run plan cannot make duplicates. After a partial failure, drop the
  creates the dry run reports as already made and apply the rest. Pass
  `repeat_ok=True` only when the user wants another copy.
- **Delete:** put the full object as `before` (for a page, its title, body
  and settings), so it can be re-created from the journal. Canvas also keeps
  deleted pages, assignments and quizzes restorable for a while at
  `/courses/N/undelete`.

Every apply writes a journal of previous values to
`~/.local/share/bulk_canvas_actions/change_log/` (macOS:
`~/Library/Application Support/bulk_canvas_actions/change_log/`). To undo,
build a new plan from the journal's `before` values (and `created` ids, for
creates) and run it the same way.

## Rules

- **One course per plan.** Copying between courses is two steps: read the
  source course, then plan writes against the destination course.
- **Deleting needs extra care.** List every item by name and id in the dry
  run. Prefer unpublishing over deleting when the user's intent allows. Never
  delete files or pages that other content may link to without saying so.
- **Deleting an assignment group deletes every assignment in it**, with no
  warning from Canvas (verified 2026-11-11). Unless the user explicitly wants
  those assignments gone, pass `group.delete(move_assignments_to=OTHER_ID)`,
  and list the assignments that move (or that would be deleted) in the plan.
- **Do not publish, and do not notify students, unless asked.** New content
  should be created unpublished unless the user says otherwise. Posting an
  announcement or sending notifications reaches students immediately and
  cannot be taken back. Do it only on an explicit request, after showing the
  exact text.
- **Content students have already submitted to:** check
  `has_submitted_submissions` before changing points, submission types, or
  grading settings, and tell the user if it is set. Moving dates is normal;
  changing how work is graded is not.
- **Untested operations go to a sandbox first.** If nothing in this skill
  covers an operation, suggest trying it on a sandbox course (no real
  students) before the real one, and ask the user which course to use. The
  sandbox run is a change like any other: dry run, the user's yes, apply.
- Stay inside what was asked. A request to "set up modules" is not license
  to rename assignments or change dates you noticed along the way. Suggest
  those separately.

## Canvas behaviour worth knowing

- **Dates are UTC in the API.** Use `parse_when()` for user input and
  `shift()` to move existing dates. `shift()` works in local time, so a change
  across a DST boundary keeps 11:59 PM. Display with `show_when()`.
- **Move a due date with its window.** Canvas rejects an assignment due date
  outside its unlock/lock dates ("must be between availability dates"), and
  the quiz API silently ignores one. When moving dates, use `shift_window()`
  to move unlock, due and lock together, and `window()` for the before value
  and the check, so the plan shows all of them and the journal keeps raw dates. If the user asked to move only the due date and that would put it
  outside the window, say so in the plan rather than letting the apply fail.
- **`due_at` on an assignment is the "Everyone" date, but only when read
  through `connect()`'s course.** Canvas's default read applies overrides for
  the requesting user, so a raw API read, or an assignment reached any other
  way (for example through a quiz's assignment from a different Canvas
  object), can return a section's date instead. `connect()` makes
  `course.get_assignment(s)` pass `override_assignment_dates=False`; read
  assignments through it. If `has_overrides` is true (`inventory assignments`
  marks these), sections have their own dates, and changing `due_at` leaves
  them alone. Say so in the plan, and move overrides too if the user means
  "everyone".
- **Classic quiz dates can be moved either way.** `course.get_assignments()`
  includes each quiz's assignment, and dates set on it carry over to the quiz
  (verified 2026-11-11), so one date plan can cover quizzes and assignments.
  For a single quiz, `quiz_tool.py settings` is simpler. Either way, move the
  whole window (`shift_window`). **Notifications** (from the canvas-lms
  source, not yet observed on a real student; treat them as likely): moving
  the due date of a *published* assignment through the assignment API sends
  students "Assignment Due Date Changed" (`BroadcastPolicies::AssignmentPolicy`);
  the same move through the quiz API (`quiz.edit` or `quiz_tool.py settings`)
  saves without broadcasting. Publishing an assignment or quiz sends "new
  assignment". Say which applies in the plan.
- **Moving a module item:**
  `item.edit(module_item={"module_id": dest.id, "position": n, "indent": k})`,
  Positions are 1-based.
- **Publishing or unpublishing a module cascades to everything in it**
  (verified 2026-11-11). `module.edit(module={"published": ...})` sets every
  item in the module *and the content it points at* (pages, assignments,
  quizzes) to the same state. Unpublishing a module therefore hides content
  that was already published, such as a quiz students are about to take. In
  the plan, list each item that will change state, by name, as its own line.
  To publish a module but keep some items hidden, unpublish those items
  afterwards, in the same plan. Adding published content to an unpublished
  module does not change the content's state.
- **Module items point at content.** Deleting an item removes it from the
  module only; deleting the page, assignment, or file it points at is a
  separate and more destructive change.
