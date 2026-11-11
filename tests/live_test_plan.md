# Live test plan: jmu-canvas against a sandbox course

Run these against a **sandbox course** (we use 2218230, "Projects in
Information Technology"). Section N needs the course published and one test
student enrolled (a colleague, with their consent); everything else works in
an unpublished course. Never read student data, including the test
student's: the FERPA rules apply here as in real use. Our tools only ever
learn *whether* a quiz has been taken.

Run each scenario the way the skill says (look, plan, dry run, apply, read
back), then check the result **independently** (`inventory`, `questions`,
or a direct read) against the pass condition. Also offline:
`python3 tests/test_*.py` and `python3 tests/mutation_check.py`.

## A. Course structure (canvas-course)

| id | request | passes when |
|---|---|---|
| A1 | "Push every due date in Week of Nov 16 back 2 days" (one item has unlock and lock dates) | dry run shows unlock/due/lock for each item; all three move together; quiz and its assignment agree; 11:59 PM kept |
| A2 | Move a quiz from Fri 3/12/2027 to Fri 3/19/2027 (crosses DST on Mar 14) | still 11:59 PM local; stored UTC goes from 04:59Z to 03:59Z |
| A2b | Move a date from October to November (crosses the fall change) | wall-clock time kept |
| A3 | "Section X gets Weekly Quiz 1 a day later" | override exists with the new date; the Everyone `due_at` read through `connect()` is unchanged; inventory marks the assignment |
| A4 | "Clear the due date on X" | `due_at` reads back null; check passes |
| A5 | Create a module at position 1, rename it, move it to 3, set an unlock date | positions 1-based; other modules renumber; unlock date reads back |
| A6 | "Students must view the page before the quiz": completion requirements and a prerequisite module | `completion_requirement` and `prerequisite_module_ids` read back as set |
| A7 | Publish a module, then unpublish it (one item inside was already published) | publishing publishes every item and its content; unpublishing unpublishes all of them, including the one that was published before. The plan must list each item that changes state |
| A8 | Move an item to another module at position 1; change its indent | item lands at 1 in the target; source module renumbers |
| A9 | Add page, external URL, assignment and file items | each item reads back with its type and target |
| A10 | Create a page, link to it from another page, rename it | slug changes; the old slug still resolves to the page; the link still works |
| A11 | Upload a file to a folder; rename, hide, move it | same file id throughout; hidden reads back true |
| A12 | Create assignments (text entry, file upload) with unlock/due/lock | submission types, extensions and all three dates read back |
| A13 | "Drop the lowest weekly quiz" | group `rules` reads back `{"drop_lowest": 1}` |
| A14 | Delete an assignment group that holds assignments | without `move_assignments_to` its assignments are gone; with it they survive in the target group. The plan lists them |
| A15 | Create a discussion topic (graded with due date; ungraded with delayed post and lock) | settings read back; no replies are read |
| A16 | Delete a module item | the content it pointed at still exists |
| A17 | Term rollover: copy a course, then shift every date (assignments, quizzes, discussions, module unlocks, delayed announcements) by N weeks | every date moves by exactly N weeks local time; nothing published that wasn't |
| A18 | Rubric: create one and attach it to an assignment | rubric reads back attached, with its criteria |
| A19 | A course with more than 100 pages or items | inventory lists all of them (paging) |

## B. Plan mechanics (ChangeSet)

| id | scenario | passes when |
|---|---|---|
| B1 | Change the course between dry run and apply | `--apply` refuses; nothing changes |
| B2 | Re-run an applied create plan; also one that mixes a create with an edit | dry run lists the create as already made; apply refuses; no duplicate in the course |
| B3 | A plan whose 2nd change raises | stops; journal shows 1 ok, 2 error, 3 absent; exit non-zero |
| B4 | Undo a date-window shift from its journal | old dates restored, including one that was blank |
| B5 | Non-ASCII names | display, plan id and journal all correct |
| B6 | A create plan that fails part-way, retried | the creates that succeeded are reported as already made; the rest apply after dropping those |
| B7 | Edit the plan script after the dry run | `--apply` with the old id refuses |

## C. Quizzes (canvas-quizzes)

| id | request | passes when |
|---|---|---|
| C1 | One question of each of the 12 types on a new quiz | each reads back as sent, checked raw (dropdown key not first, numerical range, formula answer sets) |
| C2 | LaTeX in question text and in MC answer HTML | stored; renders as an image in Canvas's preview |
| C3 | Upload an image twice with the same name; embed it | second upload renamed, first untouched; file hidden; tag in the same course; Canvas adds a verifier |
| C4 | Edit text, then points, then answers on a fresh question, within 5 minutes | each edit persists; nothing reverts |
| C5 | Change only points on a formula question, then its variable range | points edit keeps the sets; range edit regenerates them within the new range |
| C6 | Add, edit, delete, reorder on an **unpublished** quiz | quiz points/count and the gradebook assignment's points update each time |
| C7 | Same on a **published** quiz | without `--republish`: nothing republished, and the tool says to click Save; with `--republish`: republished, stays published, totals update |
| C8 | Make a group (pick 2 of 3), move questions in with `edit`, reorder with `g<ID>`, take one out with `reorder` | group numbering Q2–3; total counts pick × points; the removed question is loose |
| C9 | Settings that Canvas drops: scoring policy with 1 attempt, cant-go-back alone, due after lock | each ✗ with its reason and exit non-zero; with the needed partner setting, ✓ |
| C10 | "Put Monday Quiz in the Weekly Quiz group" | `quiz.edit(assignment_group_id=…)` moves it; assignment reads back in the group |
| C11 | Change a quiz's due date through its assignment | the quiz's own `due_at` follows |
| C12 | `find` text, regex anchored to one answer, matching right side, a question after a group | matches found; Q# as Canvas numbers them |
| C13 | Delete a question; restore it from the backup; delete a quiz that is in a module | totals drop then return; restore works (new id, at the end); module item disappears with the quiz |
| C14 | Quiz type graded → practice → graded | the plan warns; the assignment is deleted and a new one (new id) created; group kept |
| C15 | Copy questions from one quiz to another in a different course | `add` warns about image links to the other course |
| C16 | Section override on a quiz | override reads back; quiz Everyone date unchanged |

## N. Notifications and what students see (needs a published course and a test student)

Have the test student set every course notification to "Notify immediately"
and report exactly what arrives (subject, time). Never read their data
through the API; ask them.

| id | action | expected |
|---|---|---|
| N1 | `settings --published` on an unpublished quiz | one "new assignment"-type notification |
| N2 | `edit` a question on a published quiz, no `--republish` | nothing arrives; the tool tells you to click Save; student sees the old question until Save |
| N3 | Same with `--republish` | a notification arrives (this is why it is opt-in); student sees the new question |
| N4 | Click Save in Canvas after N2 with "Notify users" unticked | nothing arrives; student sees the new question |
| N5 | Move a published quiz's due date with `settings --due` | nothing arrives |
| N6 | Move a published assignment's due date through a ChangeSet plan | record what arrives (the skill must then say) |
| N7 | Publish a module that holds unpublished items | record what arrives |
| N8 | A question with an image uploaded by `upload-image` (hidden file) | the image shows for the student in the quiz |
| N9 | Student takes a quiz; then `edit`, `delete`, `reorder`, `settings --time-limit` | refused without `--force`; dates still change |

## Last run: 2026-11-11

Code version: 2.1.1, then 2.2.0 with the fixes. Scenarios A1–A16, B1–B5 and
C1–C14 were run before the fixes; after them, the scenarios touching changed
code were re-run live: A1 (dry run), A2 (redone: the first run did not
actually cross DST), B2, C3, C6, C7 (without `--republish` only, to avoid
notifying anyone), C8, C9, C12, C13. B4 ran live before the blank-date
change, which is covered offline. Not run yet: A2b, A17–A19, B6–B7 (offline
tests only), C15–C16.

Section N, run 2026-11-11 with the course published and one test student
(items named "ZZ Notify ..."; N4/N8/N9 checked through Student View):

| id | result |
|---|---|
| N1–N3, N5–N7 | **inconclusive**: actions run 09:56–09:58 (plus a quiz-API due-date change on B at 10:18), but the student received no email at all, not even for the five newly published items, which the canvas-lms source says always notify. His notification settings (probably a summary or off) filtered everything, so the run measured nothing. To redo: the student sets this course's notifications to "Notify immediately" first, then rerun on fresh items |
| N4 | pass: after Edit → Save with notify unticked, Student View showed "version 2" |
| N8 | pass: the image uploaded by `upload-image` (hidden file) displays in the quiz |
| N9 | pass: with a Student View submission, `edit`, `add`, `delete` and `settings --time-limit` are refused; `settings --due` warns and proceeds. `reorder` not checked (one-question quiz) |

Findings from the live runs:

| # | scenario | finding | change |
|---|---|---|---|
| 1 | C6 | Adding questions to an unpublished quiz left quiz and gradebook points at 0 | code: `resave()` saves unpublished quizzes |
| 2 | C8 | The resave warning miscounted quizzes with groups | code: `expected_question_count()` |
| 3 | C13 | `delete` kept no backup | code: backup and restore command |
| 4 | C4 | Dumps and backups did not round-trip (`created_at`, `*_date`); `--json` was not pure JSON | code |
| 5 | C9 | `settings` printed ✓ when Canvas silently dropped a change | code: ✗, reason, non-zero exit |
| 6 | A1 | The skill's own date example failed part-way on an item with a lock date | code + skill: `shift_window()`, `window()` |
| 7 | A1 | Plan lines cut off the lock date | code |
| 8 | B2 | Re-applying a create plan duplicated it; journals could overwrite | code: per-create guard, unique journals |
| 9 | A7 | Module publish/unpublish cascades to items and content | skill text only |
| 10 | A14 | Deleting a group deletes its assignments | skill text only |
| 11 | A3 | Assignment reads returned a section's date as `due_at` | code: `connect()` reads Everyone dates |
| 12 | C12 | `find` numbering ignored groups | code |
| 13 | C12 | `find --answers` could not anchor to one answer | code |
| 14 | C14 | Quiz type change deletes and re-creates the assignment | skill text only |
| 15 | C7 | A no-op `edit` still wrote and republished | code: skipped |
| 16 | C7 | Republishing (the old default after every change to a published quiz) notifies students, per the canvas-lms source | code: opt-in `--republish`; confirm with N1–N4 |
| 17 | C3 | `upload-image` overwrote same-named files and left them browsable | code: rename, hidden; confirm with N8 |
| 18 | C8 | No way to take a question out of a group (edit ignores a cleared id) | code: `reorder` can ungroup; `edit` refuses with the route |
