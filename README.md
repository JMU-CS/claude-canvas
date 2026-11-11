# claude-canvas

A [Claude Code](https://claude.com/claude-code) plugin, **jmu-canvas**, for
working on JMU Canvas courses from a conversation.

Ask for what you want, for example "add a short-answer question about amortized
analysis to Quiz 3" or "push every Week 5 due date back two days." Claude reads
the course first, shows you exactly what it will change, and changes it only
after you approve.

The plugin has two skills:

- **canvas-quizzes**: Classic Quiz questions and quiz settings, through
  `quiz_tool.py`. It validates questions before sending them, writes whole
  questions (Canvas silently restores stale content on partial edits), and
  reads every write back.
- **canvas-course**: the rest of a course's structure, such as modules, pages,
  files, assignments, dates and publishing. Claude writes a short script for
  each request using `canvas_course.py`. It shows you the list of changes and
  makes them only after you say yes. If anything changes in the meantime, you
  see the new list instead. Each change is read back, and the old values are
  logged in `~/.local/share/bulk_canvas_actions/change_log/` so a change can be
  undone.

## Student data (FERPA)

Claude is not approved to handle student education records, so neither skill
reads submissions, scores, grades, rosters, enrollments, user records,
analytics, messages, or discussion replies. The skills instruct Claude to stop
and hand such tasks back to you. A `PreToolUse` hook
(`plugins/jmu-canvas/hooks/ferpa_guard.py`) also blocks shell commands that
call those Canvas endpoints (including the scripts they run and the local
modules those import), and web fetches or browser navigation to Canvas pages
that show student data, such as the gradebook, SpeedGrader and People.

The hook is a backstop, not a guarantee. It reads text, so it misses code that
builds an endpoint name at run time or hides the call more than one import
deep, clicks inside a Canvas page the browser already has open, and exported
files read directly. It applies to every Claude Code session where the plugin
is enabled, so Claude also cannot run your own grade or submission scripts.
Run those yourself.

## Requirements

- Python 3.9+ and `pip install canvasapi`, in the interpreter Claude Code's
  shell uses
- A Canvas API token. At JMU you can't create one yourself; request one from a
  Canvas administrator.
- Classic Quizzes for the quiz tool. For New Quizzes (Quizzes.Next), Claude can
  script against Canvas's separate New Quizzes API, but that route is untested.

The Canvas host defaults to `https://canvas.jmu.edu` and dates to
`America/New_York`: `CANVAS_URL` in `plugins/jmu-canvas/scripts/canvas_config.py`
and `LOCAL_TZ` in `plugins/jmu-canvas/scripts/canvas_course.py`.

## Install

In the Claude Code chat box, type `/plugins` to open the plugin manager. Add the
marketplace `JMU-CS/claude-canvas`, then install `jmu-canvas` from it. The VS
Code extension also offers **Ctrl+Shift+P** → **Claude Code: Install Plugin**.

From a terminal:

```
claude plugin marketplace add JMU-CS/claude-canvas
claude plugin install jmu-canvas@jmu-cs --scope user
```

Or in `~/.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "jmu-cs": {
      "source": { "source": "github", "repo": "JMU-CS/claude-canvas" }
    }
  },
  "enabledPlugins": { "jmu-canvas@jmu-cs": true }
}
```

### Moving from canvas-quizzes

This plugin used to be `canvas-quizzes`, installed from the `JMU-CS/canvas_scripts`
marketplace. Switch with:

```
claude plugin uninstall canvas-quizzes@jmu-cs
claude plugin marketplace remove jmu-cs
claude plugin marketplace add JMU-CS/claude-canvas
claude plugin install jmu-canvas@jmu-cs --scope user
```

Your saved Canvas token carries over; it lives in your user data directory,
not in the plugin.

## Updating

Use the **Marketplaces** tab of `/plugins`, or
`claude plugin marketplace update jmu-cs` from a terminal.

## First run

Once a Canvas administrator has issued your token, keep it secret: don't paste
it into a chat and don't commit it. Save it in a file with a text editor (an
`echo` command would leave it in your shell history), make the file private,
and pass it once:

```
chmod 600 ~/canvas_key.txt
python3 <scripts>/quiz_tool.py --key-file ~/canvas_key.txt --course-id 12345 quizzes
rm ~/canvas_key.txt
```

The token is then cached, readable only by you, in
`~/.local/share/bulk_canvas_actions/config.ini` (macOS:
`~/Library/Application Support/bulk_canvas_actions/`), so the key file can be
deleted. After that, just ask, and Claude runs the tool for you.

## Using the tools directly

The scripts live in `plugins/jmu-canvas/scripts/` in this repository, or, for
a marketplace install, in
`~/.claude/plugins/cache/jmu-cs/jmu-canvas/<version>/scripts/`. Claude runs
them for you, but they work from a terminal too.

```
# browse
quiz_tool.py --key-file ~/canvas_key.txt --course-id 12345 quizzes
quiz_tool.py --course-id 12345 questions 67890
quiz_tool.py --course-id 12345 find "binary search"

# build
quiz_tool.py --course-id 12345 create "Quiz 3" --due "10/9/2026" --time-limit 30
quiz_tool.py --course-id 12345 add 67890 --file q.json --dry-run
quiz_tool.py --course-id 12345 edit 67890 111 --file changes.json --dry-run
quiz_tool.py --course-id 12345 reorder 67890 --order 111,113,g55,112 --dry-run
quiz_tool.py --course-id 12345 upload-image diagram.png --alt "call stack"

# settings
quiz_tool.py --course-id 12345 settings 67890
quiz_tool.py --course-id 12345 settings 67890 --due "10/9/2026 17:00" --shuffle
```

Dates are local (`America/New_York`, set by `LOCAL_TZ` in canvas_course.py). Accepts
`M/D/YYYY`, `M/D/YYYY HH:MM` 24-hour, `YYYY-MM-DD`, or `none` to clear. A bare
date means 11:59 PM.

Run `quiz_tool.py <subcommand> --help` for the full options.

## Safety

- A quiz that students have already **submitted is refused** unless you pass
  `--force`. The check uses Canvas's yes/no flags and never reads the
  submissions themselves.
- `edit` always writes the whole question (Canvas fills any omitted field from
  a possibly stale stored copy), reads it back, compares every field, and
  saves the previous version under
  `~/.local/share/bulk_canvas_actions/quiz_backups/`.
- `settings` is more permissive: date and title changes on a live quiz warn and
  proceed, while changes to time limit, attempts, quiz type, or scoring policy
  need `--force`. Judging by the Canvas source, date and settings changes
  made this way don't notify students, while publishing a quiz does (Canvas
  announces it as a new assignment). This hasn't yet been confirmed with a
  real student account.
- Every mutating subcommand takes `--dry-run`.
- Questions are validated before anything is sent to Canvas.
- After any change to a published quiz, students keep seeing the old version
  until the quiz is saved in Canvas. The tool tells you to open the quiz,
  click **Edit**, then **Save**. It can republish the quiz for you instead
  (`--republish`), but Canvas may then notify students of it as a new
  assignment.
- A question group that draws from a question bank is invisible to the API.
  If a quiz has one, check its place in Canvas after a `reorder`.

## Course changes directly

```
canvas_course.py --course-id 12345 inventory modules     # or assignments, groups,
                                                          # pages, files, discussions, sections
```

A plan script (see the canvas-course skill for the pattern) prints the changes
it would make. Run it again with `--apply` and it shows them again and asks
before changing anything.

`--course-id` applies to that run only once a default is saved; add
`--save-course-id` to change the saved default.

## Development

Offline tests, no Canvas access needed:

```
python3 tests/test_quiz_tool.py      # quiz_tool against a fake Canvas
python3 tests/test_canvas_course.py  # dates, plan display, apply guards
python3 tests/test_ferpa_guard.py    # what the hook blocks and allows
python3 tests/mutation_check.py      # do the tests catch deliberate breaks?
```

`tests/live_test_plan.md` lists scenarios to run against a sandbox course
after changes to either skill, with the results of the last run.

The fake Canvas mirrors the behaviour of the canvas-lms source
(`AssessmentQuestion.parse_question` and the per-type answer parsers). Changes
to how questions are written should also be tried on a sandbox course: an
unpublished course with no real students.
