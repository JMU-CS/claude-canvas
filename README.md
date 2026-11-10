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
(`plugins/jmu-canvas/hooks/ferpa_guard.py`) also blocks shell commands, and the
scripts they run, that call those Canvas endpoints. It is a backstop, not a
guarantee. It applies to every Claude Code session where the plugin is
enabled, so Claude also cannot run your own grade or submission scripts. Run
those yourself.

## Requirements

- Python 3.9+ and `pip install canvasapi`, in the interpreter Claude Code's
  shell uses
- A Canvas API token. At JMU you can't create one yourself; request one from a
  Canvas administrator.
- Classic Quizzes for the quiz tool. For New Quizzes (Quizzes.Next), Claude can
  script against Canvas's separate New Quizzes API, but that route is untested.

The Canvas host defaults to `https://canvas.jmu.edu` and dates to
`America/New_York` (`CANVAS_URL` and `LOCAL_TZ` in
`plugins/jmu-canvas/scripts/canvas_course.py`).

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
it into a chat and don't commit it. Put it in a file and pass it once; it is
cached in your own user data directory afterwards.

```
echo 'YOUR_TOKEN' > ~/canvas_key.txt && chmod 600 ~/canvas_key.txt
```

Then just ask, and Claude runs the tool for you.

## Using the tools directly

The scripts live in `plugins/jmu-canvas/scripts/`. Claude runs them for you,
but they work from a terminal too.

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

Dates are local (`America/New_York`, set by `LOCAL_TZ` in the script). Accepts
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
  need `--force`. Nothing notifies students unless you pass `--notify`.
- Every mutating subcommand takes `--dry-run`.
- Questions are validated before anything is sent to Canvas.
- After any change to a published quiz, the tool republishes it so students
  see the change. If students have already submitted, Canvas won't allow
  that, and the tool asks you to click **Save** on the quiz in Canvas instead.
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
python3 tests/test_ferpa_guard.py    # what the hook blocks and allows
```

The fake Canvas mirrors the behaviour of the canvas-lms source
(`AssessmentQuestion.parse_question` and the per-type answer parsers). Changes
to how questions are written should also be tried on a sandbox course: an
unpublished course with no real students.
