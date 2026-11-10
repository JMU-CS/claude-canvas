---
name: canvas-quizzes
description: Add, edit, or delete questions on Canvas Classic Quizzes, and change quiz settings and dates. Use when the user asks to add a question to a quiz, change a question's wording, points, or answers, remove a question, or inspect what is on a quiz. For other course changes (modules, pages, files, assignments) use canvas-course. Never for student data (submissions, scores, rosters).
---

# Editing Canvas quizzes

Use the bundled tool. It wraps the Classic Quizzes question API and refuses
unsafe writes.

**Find a way; don't report "can't".** If the tool has no direct command for
something the user wants, work out a way to get there and offer it. Options
include a sequence of existing commands, re-creating questions, or a small
script using the same helpers. Users care about the result, not whether the
API offers it in one call. Only say something is impossible after ruling out
the workarounds, and then say what the user can do instead.

**Scope.** This skill covers quiz questions and quiz settings, and nothing
else. Having a Canvas token set up for it is **not** license to change
anything else in the course. For modules, pages, files, assignments,
assignment dates, discussions, or publishing, use the `canvas-course` skill
and its plan → approve → apply procedure. Do not improvise those changes with
ad-hoc scripts from here.

## Student data is off limits (FERPA)

Claude is **not approved** to access student education records. Never fetch,
print, or summarize quiz submissions, quiz statistics (they include
per-student responses), scores, grades, rosters, enrollments, or user records,
and do not run scripts that do. That rule holds even when the user asks. If a
task needs that data, say so and stop: the user does that part themselves,
outside Claude. `quiz_tool.py` itself never reads student records. It checks
whether a quiz has been taken through Canvas's yes/no flags (`unpublishable`
on the quiz, `has_submitted_submissions` on its assignment). A plugin hook
blocks commands that call student-data endpoints. If it fires, rethink the
task; do not rephrase the command to get past it. See the `canvas-course`
skill for the full list.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/quiz_tool.py" --help
```

Always invoke it by that full path; it is not on PATH and is not in the user's
project. All examples below abbreviate it as `quiz_tool.py`.

## First run

The tool needs a Canvas API token. At JMU instructors **cannot generate one
themselves**; it has to be requested from a Canvas administrator. If the user
has no token yet, tell them that. A token is a
personal secret: never paste one into a chat message or commit it. Pass it once and it is cached
in the user's own data directory (`~/.local/share/bulk_canvas_actions/config.ini`
on Linux, equivalents elsewhere):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/quiz_tool.py" --key-file ~/canvas_key.txt --course-id 12345 quizzes
```

Requires Python 3.9+ and `pip install canvasapi`. The Canvas host defaults to
`https://canvas.jmu.edu`; for another institution edit `canvas_url` in
`scripts/canvas_config.py`.

## Facts established by testing (2026-08-28), do not re-derive

- **This works on Classic Quizzes only.** New Quizzes (Quizzes.Next) live
  behind a separate `/api/quiz/v1/` Items API that `canvasapi` does not wrap —
  its `NewQuiz` class has only `delete`, `update`, `set_attributes`, with no
  item access. `quiz_tool.py quizzes` prints a note when it sees New Quizzes
  in a course. quiz_tool does not cover them, but New Quizzes have their own
  REST Items API (`/api/quiz/v1/courses/:course_id/quizzes/:assignment_id/items`).
  If the user needs one edited, offer to script against it, and say it is
  untested here: try it on a sandbox course first, with the same full-write
  and read-back discipline.
- **Check which engine the user's course uses before promising anything.**
  Do not assume. The `quizzes` subcommand lists Classic quizzes and flags any
  New Quizzes it finds alongside them.
- **Questions can be reordered: use `quiz_tool.py reorder`.** It calls
  `POST .../reorder` with a **JSON body** (`{"order": [{"type": "question",
  "id": N}, {"type": "group", "id": G}, ...]}`). Form-encoded `order[][id]`
  returned a 500 or a do-nothing 204, which was once misread as "order is
  not writable". Verified live 2026-11-10, including a question group.
- **The `position` field in question JSON is not the display position.** It
  is a copy inside the stored question data and is usually null. The real
  position is a database column that the API does not return, but both the
  questions endpoint and the quiz page sort by it (canvas-lms
  `QuizQuestionsController#index`, `Quiz#root_entries`). So the endpoint's
  order is the display order. **Never sort by the JSON `position`.** New
  questions land at the end; reorder afterwards to put them elsewhere.
- **Question groups:** `questions` shows each group as a block (`g<ID>`) with
  its members under it. In `reorder`, a group moves as one entry. A group that
  draws from a question bank has no questions of its own, so the API cannot
  see it. Ask the user to check its place in the web UI.
- **Canvas numbers only non-`text_only` questions.** Confirmed: 14 items with
  one text-only reports `question_count` 13, so Canvas's "Q10" is the 11th
  item. `quiz_tool.py questions` prints Canvas's Q# column — quote that, and
  prefer question ids when precision matters.
- **`data-code-text` on pasted code blocks is often STALE.** Questions pasted
  from an AI chat widget carry `<div class="epitaxy-...">` wrappers, per-token
  `<span>`s, and a `data-code-text` attribute. That attribute can contain
  entirely different code than the visible `<pre>` (observed in the wild: the
  attribute said TreeMap/banana while the visible code said HashMap/"a").
  Reconstruct from
  the visible `<pre>`, and only use the attribute after confirming it matches
  ignoring whitespace. Replace the whole mess with a plain `<pre>`.
- **`question_count` lags briefly** after a write, then settles. The tool
  counts questions directly instead of trusting it. Not a problem.
- **`text_only_question` is excluded** from Canvas's own `question_count` but
  is a real item on the quiz.
- **Question banks are untested.** Questions in a question bank (as opposed
  to a quiz's own questions) have not been tried. Tell the user it is
  uncharted before editing one.

## Edits always write the whole question (learned the hard way, 2026-11-05)

**Never send a partial update to Canvas, by any route.** When a question update
leaves a field out, or sends it as null, Canvas fills it from the question's
**stored copy** (its linked `assessment_question`), not from the current quiz
question. The source: `AssessmentQuestion.parse_question` builds the new
question as `stored_copy.merge(sent.compact)`. Canvas often does **not**
update the stored copy when the question is edited
(`AssessmentQuestion#editable_by?`), so it can hold an older version
indefinitely. It is frozen when it is shared by several quiz questions, when it
sits in a named question bank, or when the question's first edit came 30 s to
5 min after the question was created. **Treat every stored copy as possibly
stale, especially on quizzes copied from an earlier semester.**
Reproduced live 2026-11-10 on a copied scratch quiz: a text-only edit followed
by a points-only edit put the original text back. The tool's full writes kept
the new text through the same sequence. On a real course this swapped the
previous semester's content into a quiz the day before it was given:
sending only `answers` brought back the old text, type and points (leaving a
true/false question with 5 answers that the web editor could not open). Sending
only `question_text` brought back old answers.

`quiz_tool.py edit` handles this:

- The payload may name only the fields to change. The tool reads the live
  question, replaces those fields, and sends **every** field: name, type, text,
  points, all six comment fields, and the full answer list. None of them is
  sent as null.
- It checks the **complete** result. For example, a true/false question with
  5 answers is refused before anything is sent.
- It re-reads the question just before writing, and refuses to write if the
  question changed since the edit was prepared.
- After writing it **reads the question back and compares every field**. A
  mismatch exits non-zero with the differences listed. Treat that as an
  emergency: tell the user, and point them at the backup file the tool saved.
  A backup of the previous version is saved to
  `~/.local/share/bulk_canvas_actions/quiz_backups/` before every edit.

**Guard against a colleague editing the same quiz** (shared course shells that
several instructors edit). `questions --json` prints a `_fingerprint` with each question.
Leave it in a payload built from the dump, or pass `--expect FINGERPRINT`
(printed by `edit --dry-run`). The tool then refuses to write if anyone
changed the question since it was read.

Workflow for "change question X":

1. `questions QUIZ_ID --json`, then find the question by id.
2. Write a payload with the fields to change. Either copy the question's
   `_fingerprint` into it, or edit the whole dumped object, which round-trips:
   read-only fields such as `id` and `position` are dropped automatically.
3. `edit QUIZ_ID QUESTION_ID --file q.json --dry-run`. Show the user the
   changes it prints.
4. On approval, run it without `--dry-run`, plus `--expect` if the payload has
   no `_fingerprint`. Fingerprints are for you; never show them to the user
   or ask them to handle one. Report the read-back result and do not paraphrase it as
   success.

## Workflow for "add a question about X to quiz Y"

1. `python quiz_tool.py --course-id N quizzes` to find the quiz id.
2. `python quiz_tool.py --course-id N questions QUIZ_ID` — **always do this
   first.** Match the existing questions' style: they tend to use `<p>` wrapped
   HTML, `<code>`/courier spans for identifiers, and images uploaded to Canvas
   files. Question names are often just `'Question'`.
3. Write the payload to a JSON file in the scratchpad, not the repo.
4. `... add QUIZ_ID --file q.json --dry-run` first.
5. Show the user the rendered question text and the answer key, then run
   without `--dry-run` on their approval.
6. If it should not be last, `reorder` it (below).

## Reordering

```bash
quiz_tool.py --course-id N reorder QUIZ_ID --order x --dry-run   # prints the current order
quiz_tool.py --course-id N reorder QUIZ_ID --order 901,903,g55,902 --dry-run
quiz_tool.py --course-id N reorder QUIZ_ID --order 901,903,g55,902
```

`--order` lists every top-level question id, plus `g<ID>` for each group,
exactly once. The dry run shows the old and new position of each entry. The
tool reads the order back after writing and republishes a published quiz
(see below).

## Creating quizzes, searching, and settings

```bash
# make a quiz (unpublished unless --publish)
quiz_tool.py --course-id N create "Quiz 3" --due "10/9/2026" --time-limit 30 --shuffle

# find a question without knowing which quiz it is on
quiz_tool.py --course-id N find "binary search"
quiz_tool.py --course-id N find --answers --regex "O\(n log n\)"

# show every setting, or change some
quiz_tool.py --course-id N settings QUIZ_ID
quiz_tool.py --course-id N settings QUIZ_ID --due "10/9/2026 17:00" --shuffle
```

**Dates are local (America/New_York) and converted to UTC for Canvas.** Accepts
`M/D/YYYY`, `M/D/YYYY HH:MM` (24-hour), `YYYY-MM-DD[ HH:MM]`, or `none` to
clear. A bare date means 11:59 PM, which is what a due date almost always
means. DST is handled. Change `LOCAL_TZ` in quiz_tool.py for another zone.

**`settings` treats dates differently from structure.** Changing `due_at`,
`unlock_at`, `lock_at`, `title`, `description`, `access_code`,
`shuffle_answers`, `show_correct_answers`, or `published` on a quiz with
submissions prints a warning and proceeds — moving a due date is a normal
thing to do mid-semester. Changing `quiz_type`, `time_limit`,
`allowed_attempts`, `scoring_policy`, `one_question_at_a_time`, or
`cant_go_back` alters the conditions students were graded under and needs
`--force`.

Settings changes do not notify students unless you pass `--notify`.

**`find` searches question text and names; add `--answers` to search answer
text too.** The output says where each match landed (`[match in body]`,
`[match in answers]`, `[match in name]`). Without `--regex` the pattern is
matched literally, so do not add backslash escapes.

## Safety rules

- **A quiz with student submissions is refused** unless `--force`. Do not
  pass `--force` unless the user explicitly says to after being told the quiz
  has been taken. Editing a taken quiz leaves already-graded attempts
  inconsistent. The tool reports only *whether* students have submitted,
  never who or how many.
- Mid-semester, most quizzes in a live course have submissions. Expect the
  block; treat it as working correctly, not as an obstacle to route around.
- `delete` prompts for confirmation unless `--yes`.

## Question payload shapes

`answers` uses `weight: 100` for correct, `0` for incorrect.

Canvas Classic Quizzes has **12** question types. A survey of 634 real
questions found 9 of them in active use; `matching`, `numerical`,
and `calculated` were absent, so they get less real-world exercise.

| type | answers | tested? |
|---|---|---|
| `multiple_choice_question` | exactly one weight-100 | yes |
| `multiple_answers_question` | one or more weight-100 | yes |
| `true_false_question` | exactly two, texts `True` and `False` | yes |
| `short_answer_question` | all accepted spellings at weight 100 | yes |
| `fill_in_multiple_blanks_question` | weighted **plus `blank_id`**, matching `[blank_id]` in the text | yes |
| `multiple_dropdowns_question` | same, one answer set per `blank_id` | yes |
| `essay_question` | none (hand graded) | yes |
| `text_only_question` | none; used as a section header | yes |
| `file_upload_question` | none (hand graded) | yes |
| `matching_question` | `answer_match_left` / `answer_match_right` | yes |
| `numerical_question` | `numerical_answer_type` + `answer_exact`/`answer_error_margin`, `answer_range_start`/`answer_range_end`, or `answer_approximate`/`answer_precision` | yes |
| `calculated_question` | `formulas` + `variables`; the tool generates the answer sets (see below) | yes |

Answers may use either the read names (`text`, `weight`, `html`, `left`,
`right`, `exact`, `comments`, and so on, as the read API and `questions --json`
return them) or the write names (`answer_text`, `answer_weight`,
`answer_html`, `answer_match_left`, `answer_exact`, `answer_comment`, ...). The
tool rewrites every answer to the write names before sending, because several
of Canvas's answer parsers read **only** the write names (see below).
Unknown answer keys are dropped with a note, so read the note: it is how a
typo shows up.

### Published quizzes serve a stale snapshot

Students take a published quiz from a frozen snapshot (`quiz_data`). Canvas
regenerates it only when the quiz's published state changes, or when someone
clicks Save in the web UI (canvas-lms `Quiz#generate_quiz_data_on_publish`).
So **any** question change made through the API (add, edit, delete, reorder)
is invisible to students until then. `question_count` and `points_possible`
stay stale as well. Verified: a published quiz with 13 added questions
reported `question_count=0` until republished.

After every change, `resave()` in quiz_tool.py unpublishes and republishes a
published quiz. If students have submitted, Canvas refuses to unpublish, so
the tool tells the user to open the quiz and click **Save**. Relay that
plainly. Until they do, students see the old version. Unpublished quizzes need
nothing.

### File upload questions cannot be tested from Preview

`file_upload_question` does not work on the quiz Preview screen; the dialog
appears but nothing uploads. Testing one requires the quiz to be **published**
and taken through **Student View**. To do that without exposing anything to
real students, put the quiz in a course whose own `workflow_state` is
`unpublished` — students cannot reach any content there, so a quiz inside it
can be safely published. Find one with the `quizzes` subcommand against a
candidate course, or ask the user which sandbox course they want to use. Note
separately that the
`one_question_at_a_time` quiz setting is independently known to break file
upload questions, so leave it false when testing.

### Answer ordering is not yours to control

Two separate behaviours, easy to confuse:

- **Dropdown options display in the order they are stored** when
  `shuffle_answers` is off. The tool sends them in your order (it no longer
  moves the correct option first), so do not put the correct option first in
  every blank.
- **Matching questions always shuffle their right-hand options**, regardless of
  the `shuffle_answers` setting. That is Canvas by design, not something the
  payload controls. Do not try to fix a "weird" matching order — the stored
  `answer_match_left`/`answer_match_right` pairs are what matter, and the
  display order is Canvas's.

### Read names are silently ignored on write

Canvas's answer parsers (canvas-lms
`app/models/quizzes/quiz_question/answer_parsers/`) differ in which names they
read. A wrong name is accepted with no error and stored as empty or 0:

- **`multiple_dropdowns_question` reads only `answer_weight`.** Sending
  `weight` stores 0 everywhere, and Canvas then marks the first option of each
  blank correct. This was once misread as "Canvas ignores weights on blanks".
- **`matching_question` reads only `answer_match_left`/`answer_match_right`.**
- **Numerical reads only `answer_exact`, `answer_error_margin`,
  `answer_range_start`, `answer_range_end`, `answer_approximate`,
  `answer_precision`.** `exact=42` is stored as 0.0.
- **`multiple_answers_question` reads answer HTML only from `answer_html`.**

`canonical_answer()` in quiz_tool.py rewrites every answer to these names, and
answers are sent as an indexed hash (`question[answers][0][answer_weight]`).
Verified live 2026-11-10: a dropdown sent with `answer_weight` keeps a
correct option that is not listed first. The same answers sent the old way
(canvasapi, `weight`) stored the first option as correct. The read-back check
after every add and edit would catch any field that still did not stick.

Example (dropdowns, the fiddliest common case):

```json
{"question_name": "Doubling", "question_type": "multiple_dropdowns_question",
 "question_text": "<p>Doubling gives [amort] amortized and [worst] worst case.</p>",
 "points_possible": 2,
 "answers": [{"text": "O(1)", "weight": 100, "blank_id": "amort"},
             {"text": "O(n)", "weight": 0,   "blank_id": "amort"},
             {"text": "O(n)", "weight": 100, "blank_id": "worst"},
             {"text": "O(1)", "weight": 0,   "blank_id": "worst"}]}
```

The validator rejects bad payloads before anything reaches Canvas, so a
validation error costs nothing — read it and fix the JSON.

### Formula (calculated) questions

Each student gets one **answer set**: one choice of variable values plus the
correct answer for those values. Canvas grades against the stored sets and
does not evaluate the formula, and the API will not generate sets. So the
tool picks values in each variable's range and computes the answers itself:

```json
{"question_name": "Comparisons", "question_type": "calculated_question",
 "points_possible": 1,
 "question_text": "<p>Selection sort on [n] items makes how many comparisons?</p>",
 "formulas": ["n*(n-1)/2"],
 "variables": [{"name": "n", "min": 5, "max": 30, "scale": 0}],
 "answer_tolerance": 0, "formula_decimal_places": 0, "answer_count": 10}
```

- Every variable must appear in the text as `[name]`. `scale` is the number
  of decimal places in the generated values (0 means integers).
- Formulas use `+ - * / ^ %`, parentheses, `pi`, `e`, and `abs sqrt ln log
  exp sin cos tan asin acos atan ceil floor round min max fact comb perm`.
  `log(x)` is base 10 and `log(x, b)` takes a base. Earlier lines may define
  intermediate values (`m = a + b`); the last line is the answer.
- `answer_tolerance` is how far from the answer a student may be (a number,
  or a percentage string such as `"1%"`).
- Editing the formula, the variables, or the decimal places regenerates the
  sets; editing anything else keeps them. You can also supply `answers`
  yourself: `[{"variables": {"n": 7}, "answer_text": 21}, ...]`.
- If someone later clicks Generate in the web editor, Canvas recomputes the
  sets with its own evaluator. Stick to the syntax above so the two agree.

## Math notation: LaTeX where it renders, Unicode where it doesn't

Never write bare ASCII math like `2^n`, `O(n^2)`, or `Theta(n log n)` into a
question. Pick by field:

### Rich fields — use LaTeX

`question_text`, and answer text supplied as **`answer_html`**. Canvas renders
LaTeX server-side as an image. Generate the markup, never hand-encode it (the
src is the LaTeX percent-encoded *twice*):

```bash
quiz_tool.py --course-id N equation 'O(n \log_2 n)' '2^n'
```

For a choice-type answer, set **both** fields — `answer_html` with the LaTeX
and `answer_text` with a Unicode fallback:

```json
{"answer_html": "Running time is <img class=\"equation_image\" ...>",
 "answer_text": "Running time is O(n log\u2082 n)",
 "answer_weight": 100}
```

Canvas's own editor writes a `text` that is just the HTML with tags stripped,
so several answers end up with the identical useless fallback
`"The worst-case running time is in ."`. Supplying `answer_text` yourself avoids
that.

### Plain fields the student *selects* — use Unicode

`multiple_dropdowns_question` options, `matching_question`
`answer_match_left`/`answer_match_right`, and `question_name`. These are not
rendered as HTML, so write `2ⁿ`, `n²`, `log₂ n`, `Θ(n log n)`.

Both fail silently rather than erroring, in different ways, so do not test this
by seeing whether the write succeeds:

- **Dropdowns discard answer HTML.** Sending `answer_html` on a
  `multiple_dropdowns_question` answer stores nothing — it reads back as null,
  and only `text` survives.
- **Matching stores markup verbatim and never renders it.** An `<img>` in
  `answer_match_right` is kept exactly as sent, so the student sees the raw tag
  as literal text.

Rendered LaTeX in answers appears only on `multiple_choice_question` and
`multiple_answers_question` in real content (73 answers surveyed). The
validator rejects answer HTML on blanks-style questions and markup on matching
sides.

| | |
|---|---|
| superscript | `⁰¹²³⁴⁵⁶⁷⁸⁹ ⁿⁱᵏᵐᵃᵇᶜᵈᵉᵍʰʲˡᵒᵖʳˢᵗᵘᵛʷˣʸᶻ ⁺⁻⁼⁽⁾` |
| subscript | `₀₁₂₃₄₅₆₇₈₉ ₙₖᵢⱼₘₚₛₜ` |
| greek | `Θ Ω α β γ δ Δ ε θ λ μ π σ φ ω` |
| other | `∑ ∏ √ ∞ ≈ ≠ ≤ ≥ ± × ÷ · → ⇒ ∈ ∉ ⊆ ⊂ ∪ ∩ ¬ ∧ ∨ ⌈ ⌉ ⌊ ⌋ …` |

There is no superscript `q` and no superscript for most capitals. If a formula
needs one, it belongs in a rich field as LaTeX instead.

### Plain fields the student *types* — use plain ASCII

`short_answer_question` answers and `fill_in_multiple_blanks_question` answers
are matched against what the student types on a keyboard. **Unicode is wrong
here** — nobody types `2ⁿ`. Accept the ASCII forms, and list the plausible
variants, since matching is literal:

```json
"answers": [{"text": "O(n^2)",  "weight": 100},
            {"text": "O(n*n)",  "weight": 100},
            {"text": "n^2",     "weight": 100}]
```

The question text asking for it can still use LaTeX; only the accepted answers
must be typeable.

## Images in questions

`question_text` is HTML, so an image is just an `<img>` tag. Two rules matter.

**The image must live in the same course as the quiz.** A Canvas file URL is
course-scoped. A question copied from last semester keeps the old course's file
URLs, which still render for the instructor (who can read both courses) and are
broken for every student in the new one. `add`/`edit` warn when a payload
references a different course's files.

**Upload first, then reference.** The `upload-image` subcommand puts a local
file in the course and prints the exact tag to paste:

```bash
quiz_tool.py --course-id 12345 upload-image diagram.png --alt "call stack"
```

It writes to a `quiz_images` folder by default (`--folder` to change) and sets
`width` from the image, capped at 700 so wide images don't overflow the
question column. Then embed the printed tag in `question_text`.

The tag it emits uses the course-scoped preview URL:

```html
<img src="https://canvas.jmu.edu/courses/12345/files/67890/preview"
     alt="call stack" width="420" loading="lazy"
     data-api-endpoint="https://canvas.jmu.edu/api/v1/courses/12345/files/67890"
     data-api-returntype="File">
```

Do **not** try to construct a `?verifier=` token. Canvas adds one itself when
it stores the question and rewrites these links per viewer. Verified: a tag
saved without a verifier came back with one attached.

**Math does not need an image file.** Canvas renders LaTeX server-side via
`equation_images`, which is how the existing questions show things like
&Theta;(n). Copy that markup from an existing question rather than uploading a
rendered formula.

## `--course-id` is per run

`--course-id` is saved as the default only when no default is saved yet. After
that it applies to the one run, and the saved default (shared with the user's
other `canvas_config` scripts) stays as it is. Changing the default takes an
explicit `--save-course-id`. Do not pass that flag unless the user asks. Still
pass `--course-id` explicitly on every call rather than relying on the saved
default.
