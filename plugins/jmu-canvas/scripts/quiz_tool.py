"""
Inspect and edit questions on Canvas Classic Quizzes.

Designed to be driven conversationally (including by Claude Code): read a quiz
to see its existing style, draft a question as JSON, then add it.

    python quiz_tool.py quizzes
    python quiz_tool.py questions 5011166
    python quiz_tool.py add 5011166 --file new_question.json --dry-run
    python quiz_tool.py add 5011166 --file new_question.json

Quizzes that already have student submissions are refused unless --force is
given, because editing a taken quiz leaves scores inconsistent.

NOTE: This works on Classic Quizzes only. New Quizzes (Quizzes.Next) store
items behind a separate /api/quiz/v1/ Items API that canvasapi does not wrap.
"""

import argparse
import datetime
import hashlib
import html as html_module
import json
import pathlib
import re
import sys
import time
import urllib.parse
from canvas_config import get_canvas_and_course, get_datadir

# Dates on the command line are read and displayed in canvas_course.LOCAL_TZ.
from canvas_course import CANVAS_URL, LOCAL_TZ, parse_when, show_when  # noqa: E402

# The 12 question types Canvas Classic Quizzes accepts, mapped to how their
# answers work. Verified against the Canvas API docs for question_type.
#   none       - no answers at all
#   weighted   - answers are {text, weight}; weight 100 marks a correct answer
#   blanks     - weighted answers that must also carry a blank_id
#   matching   - answers are {answer_match_left, answer_match_right}
#   numerical  - answers carry numerical_answer_type and its numeric fields
#   free       - hand-graded or ungraded; answers optional
ANSWER_STYLE = {
    "multiple_choice_question": "weighted",
    "true_false_question": "weighted",
    "multiple_answers_question": "weighted",
    "short_answer_question": "weighted",
    "fill_in_multiple_blanks_question": "blanks",
    "multiple_dropdowns_question": "blanks",
    "matching_question": "matching",
    "numerical_question": "numerical",
    "essay_question": "free",
    "file_upload_question": "free",
    "text_only_question": "none",
    "calculated_question": "free",
}

# Types actually exercised against Canvas. The rest are built from the API
# docs and have never been round-tripped, so treat a failure on one of them as
# a bug here rather than as the user's mistake.
TESTED_TYPES = {
    "multiple_choice_question", "true_false_question", "multiple_answers_question",
    "short_answer_question", "fill_in_multiple_blanks_question",
    "multiple_dropdowns_question", "essay_question", "text_only_question",
    "matching_question", "numerical_question", "file_upload_question",
}

# calculated_question needs variables/formulas/answer_tolerance/
# formula_decimal_places, none of which the create/update endpoint documents as
# writable. Reading one works; building one through the API probably does not.
UNSUPPORTED_TYPES = {
    "calculated_question": "Canvas does not document variables/formulas as "
                           "writable fields, so a formula question almost "
                           "certainly cannot be built through the API. "
                           "Create it in the web UI instead.",
}

# ---------------------------------------------------------------------------
# Answer field names
#
# The read API and the write API use different names for the same answer
# fields, and Canvas's answer parsers are inconsistent about which names they
# accept (canvas-lms app/models/quizzes/quiz_question/answer_parsers/):
#
#   - multiple_dropdowns reads ONLY answer_weight. Sending `weight` stores 0,
#     and Canvas then marks the first option of each blank correct. This was
#     the real cause of the old "Canvas ignores weights on blanks" note.
#   - matching reads ONLY answer_match_left / answer_match_right; the read
#     names left / right are ignored.
#   - numerical reads ONLY answer_exact etc.; exact=42 is stored as 0.0.
#   - answer comments are answer_comment / answer_comment_html on write.
#
# So every answer is rewritten to the write names before it is sent, whatever
# names the payload used.
# ---------------------------------------------------------------------------
ANSWER_READ_TO_WRITE = {
    "text": "answer_text",
    "html": "answer_html",
    "weight": "answer_weight",
    "comments": "answer_comment",
    "comments_html": "answer_comment_html",
    "left": "answer_match_left",
    "right": "answer_match_right",
    "left_html": "answer_match_left_html",
    "exact": "answer_exact",
    "margin": "answer_error_margin",
    "start": "answer_range_start",
    "end": "answer_range_end",
    "approximate": "answer_approximate",
    "precision": "answer_precision",
}
ANSWER_WRITE_TO_READ = {w: r for r, w in ANSWER_READ_TO_WRITE.items()}
# Write names that are the same on read.
ANSWER_SAME_NAME = {"id", "blank_id", "match_id", "numerical_answer_type"}
ANSWER_WRITE_FIELDS = set(ANSWER_WRITE_TO_READ) | ANSWER_SAME_NAME
# Older spellings people use, mapped to the name Canvas reads.
ANSWER_ALIASES = {"answer_comments": "answer_comment",
                  "answer_comments_html": "answer_comment_html"}

ANSWER_TEXT_KEYS = ("answer_text",)
ANSWER_HTML_KEYS = ("answer_html",)
ANSWER_WEIGHT_KEYS = ("answer_weight",)

# Numerical read names, kept for the numerical validation messages.
NUMERICAL_ALIASES = {r: w for r, w in ANSWER_READ_TO_WRITE.items()
                     if w.startswith(("answer_exact", "answer_error",
                                      "answer_range", "answer_approx",
                                      "answer_precision"))}

# Required write-side fields per numerical_answer_type.
NUMERICAL_REQUIRED = {
    "exact_answer": ("answer_exact",),          # answer_error_margin optional
    "range_answer": ("answer_range_start", "answer_range_end"),
    "precision_answer": ("answer_approximate", "answer_precision"),
}


def canonical_answer(answer, qtype, dropped):
    """One answer rewritten to the field names Canvas reads on write.

    Read-format answers (as `questions --json` dumps them) and write-format
    answers both come out the same way. Empty values are left out: answers are
    always sent as a complete list, so a missing field inside one answer cannot
    be filled from a stale copy. Unknown keys are dropped and recorded in
    `dropped` so a typo is reported rather than silently ignored.
    """
    style = ANSWER_STYLE.get(qtype)
    out = {}
    # Write names win over read names if a payload carries both.
    for key in sorted(answer, key=lambda k: k in ANSWER_READ_TO_WRITE):
        value = answer[key]
        name = ANSWER_ALIASES.get(key, key)
        name = ANSWER_READ_TO_WRITE.get(name, name)
        if name not in ANSWER_WRITE_FIELDS:
            dropped.add(key)
            continue
        if name in out:
            continue
        if value is None:
            continue
        if value == "" and name not in ("answer_text",):
            continue
        out[name] = value

    if style == "matching":
        # The read API repeats the left side as text/html; matching reads
        # only answer_match_left(_html).
        out.pop("answer_text", None)
        out.pop("answer_html", None)
        out.pop("answer_weight", None)
    elif style == "numerical":
        # Canvas forces numerical answers to weight 100.
        out.pop("answer_weight", None)
        out.pop("answer_html", None)
    elif style == "blanks":
        out.pop("match_id", None)
    if style != "matching":
        out.pop("answer_match_left", None)
        out.pop("answer_match_right", None)
        out.pop("answer_match_left_html", None)
    if style != "numerical":
        for name in list(out):
            if name in NUMERICAL_ALIASES.values() or name == "numerical_answer_type":
                out.pop(name)
    return out


def prepare(question, where="question"):
    """Rewrite a payload to the names Canvas reads, in place, and return it.

    Read-only fields that appear in a `questions --json` dump are dropped so a
    dump can be edited and sent back as-is.
    """
    for key in READ_ONLY_FIELDS:
        question.pop(key, None)
    question.pop("_fingerprint", None)
    if "answers" in question:
        dropped = set()
        question["answers"] = [canonical_answer(a, question.get("question_type"),
                                                dropped)
                               for a in (question["answers"] or [])]
        if dropped:
            print(f"note: {where}: ignored answer field(s) "
                  f"{', '.join(sorted(dropped))}; Canvas does not read them",
                  file=sys.stderr)
    return question


# Fields the create/update endpoint reads (canvas-lms
# AssessmentQuestion.parse_question). Anything else is discarded by Canvas.
WRITABLE_FIELDS = {
    "question_name", "question_text", "question_type",
    "points_possible", "correct_comments", "incorrect_comments",
    "neutral_comments", "correct_comments_html", "incorrect_comments_html",
    "neutral_comments_html", "text_after_answers", "answers", "quiz_group_id",
    "matching_answer_incorrect_matches",
}

# Fields a `questions --json` dump carries that cannot be written. They are
# dropped from payloads rather than rejected, so a dump round-trips.
# position is listed as writable in the API docs but Canvas ignores it.
READ_ONLY_FIELDS = {
    "id", "quiz_id", "position", "course_id", "assessment_question_id", "name", "matches",
    "variables", "formulas", "answer_tolerance", "formula_decimal_places",
}

# The text fields sent with every edit. Canvas fills any field that is missing
# (or null) from the question's stored copy, so each must be sent, as "" if
# empty. See full_question().
COMMENT_FIELDS = ("correct_comments", "incorrect_comments", "neutral_comments")
TEXT_FIELDS = (("question_name", "question_text")
               + COMMENT_FIELDS + tuple(f + "_html" for f in COMMENT_FIELDS))


def answer_field(answer, keys):
    """Return the first of `keys` present in an answer dict, else None."""
    for k in keys:
        if k in answer:
            return answer[k]
    return None


def has_answer_field(answer, keys):
    return any(k in answer for k in keys)


# Quiz settings this tool can read and write, with the type each one takes.
QUIZ_SETTINGS = {
    "title": str,
    "description": str,
    "quiz_type": str,          # assignment | practice_quiz | graded_survey | survey
    "time_limit": int,         # minutes
    "allowed_attempts": int,   # -1 for unlimited
    "scoring_policy": str,     # keep_highest | keep_latest
    "shuffle_answers": bool,
    "one_question_at_a_time": bool,
    "cant_go_back": bool,
    "show_correct_answers": bool,
    "access_code": str,
    "published": bool,
    "due_at": "date",
    "unlock_at": "date",
    "lock_at": "date",
}

# Changing these after students have attempted the quiz alters the conditions
# they were graded under, so they need --force. Dates and cosmetics do not.
RISKY_WITH_SUBMISSIONS = {
    "quiz_type", "time_limit", "allowed_attempts", "scoring_policy",
    "one_question_at_a_time", "cant_go_back",
}


# Canvas treats a bracketed identifier as a blank: [low1], [b2], [order].
# Anything else in brackets is ordinary text -- Java array indexing (A[mid]) and
# array literals ([3, 6, 9]) must not be mistaken for blanks.
BLANK_RE = re.compile(r"\[([A-Za-z_]\w*)\]")

# An HTML tag, as opposed to a bare "<" used as less-than.
MARKUP_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")


def in_answers_ids(answers):
    """blank_ids referenced by an answer list."""
    return {a.get("blank_id") for a in answers if a.get("blank_id")}


def blanks_in(text):
    """Bracketed identifiers anywhere in the text."""
    return set(BLANK_RE.findall(text or ""))


def blanks_outside_code(text):
    """Bracketed identifiers ignoring <pre> and <code> spans.

    Brackets inside a code sample are almost always indexing, not a blank, so
    they must not be reported as blanks needing answers.
    """
    stripped = re.sub(r"<(pre|code)\b[^>]*>.*?</\1>", " ", text or "",
                      flags=re.S | re.I)
    return blanks_in(stripped)


def strip_html(text):
    """Plain text from question HTML, adequate for searching and listing."""
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", " ", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
                         ("&mdash;", "-"), ("&ndash;", "-")):
        text = text.replace(entity, char)
    return " ".join(text.split())


def die(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def load_questions(path):
    """Read a question payload from a JSON file (or stdin if path is '-').

    Accepts either a single question object or a list of them.
    """
    text = sys.stdin.read() if path == "-" else open(path).read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        die(f"could not parse JSON from {path}: {e}")
    return data if isinstance(data, list) else [data]


def validate(question, index):
    """Return a list of human-readable problems with a question payload."""
    where = f"question[{index}]"
    problems = []

    unknown = set(question) - WRITABLE_FIELDS
    if unknown:
        problems.append(f"{where}: unsupported field(s): {', '.join(sorted(unknown))}")

    qtype = question.get("question_type")
    if not qtype:
        problems.append(f"{where}: missing question_type")
        return problems
    if qtype not in ANSWER_STYLE:
        problems.append(f"{where}: unknown question_type '{qtype}' "
                        f"(known: {', '.join(sorted(ANSWER_STYLE))})")
        return problems

    if not question.get("question_text"):
        problems.append(f"{where}: missing question_text")

    if qtype in UNSUPPORTED_TYPES:
        problems.append(f"{where}: {qtype} is not creatable through the API. "
                        f"{UNSUPPORTED_TYPES[qtype]}")
        return problems

    style = ANSWER_STYLE[qtype]
    answers = question.get("answers") or []

    if style == "none":
        if answers:
            problems.append(f"{where}: {qtype} takes no answers")
        return problems

    if style == "free":
        return problems

    if not answers:
        problems.append(f"{where}: {qtype} requires answers")
        return problems

    if style in ("weighted", "blanks"):
        for j, a in enumerate(answers):
            if not (has_answer_field(a, ANSWER_TEXT_KEYS)
                    or has_answer_field(a, ANSWER_HTML_KEYS)):
                problems.append(f"{where}.answers[{j}]: missing 'text' "
                                "(or 'answer_html')")
            if not has_answer_field(a, ANSWER_WEIGHT_KEYS):
                problems.append(f"{where}.answers[{j}]: missing 'weight' "
                                "(100 = correct, 0 = incorrect)")
            elif not isinstance(answer_field(a, ANSWER_WEIGHT_KEYS), (int, float)):
                problems.append(f"{where}.answers[{j}]: weight must be a "
                                "number (100 = correct, 0 = incorrect)")
            if style == "blanks" and not a.get("blank_id"):
                problems.append(f"{where}.answers[{j}]: {qtype} requires 'blank_id'")

        correct = [a for a in answers
                   if answer_field(a, ANSWER_WEIGHT_KEYS) == 100]
        if not correct:
            problems.append(f"{where}: no answer has weight 100, so nothing is correct")
        if (qtype in ("multiple_choice_question", "multiple_answers_question")
                and len(answers) < 2):
            problems.append(f"{where}: {qtype} needs at least two answers")
        if qtype == "multiple_choice_question" and len(correct) > 1:
            problems.append(f"{where}: multiple_choice_question has {len(correct)} "
                            "correct answers; use multiple_answers_question instead")
        if qtype == "true_false_question":
            texts = sorted(str(answer_field(a, ANSWER_TEXT_KEYS) or "").strip().lower()
                           for a in answers)
            if texts != ["false", "true"]:
                problems.append(f"{where}: true_false_question needs exactly two "
                                "answers, 'True' and 'False'")

        if style == "blanks":
            groups = {}
            for a in answers:
                groups.setdefault(a.get("blank_id"), []).append(a)
            for blank, group in groups.items():
                correct = [a for a in group
                           if answer_field(a, ANSWER_WEIGHT_KEYS) == 100]
                if not correct:
                    problems.append(f"{where}: blank '{blank}' has no answer "
                                    "with weight 100")
            # Every [blank] in the text needs answers, and vice versa.
            text = question.get("question_text") or ""
            # A blank needing answers is one in prose: brackets inside a code
            # sample are indexing, not a blank.
            for missing in sorted(blanks_outside_code(text) - in_answers_ids(answers)):
                problems.append(f"{where}: blank [{missing}] appears in the text "
                                "but has no answers")
            # An orphaned answer is judged against the whole text, so a blank
            # deliberately placed inside <code> still counts as present.
            for extra in sorted(in_answers_ids(answers) - blanks_in(text)):
                problems.append(f"{where}: blank_id '{extra}' has answers but "
                                f"no [{extra}] in the question text")

    if style == "blanks":
        # Canvas discards answer HTML for blank/dropdown answers: they render as
        # plain <option> text. Verified -- html came back null.
        for j, a in enumerate(answers):
            if has_answer_field(a, ANSWER_HTML_KEYS):
                problems.append(
                    f"{where}.answers[{j}]: Canvas discards answer HTML for "
                    f"{qtype}; the option renders as plain text. Put the "
                    "notation in 'text' using Unicode (2\u207f, n\u00b2, "
                    "log\u2082 n) instead.")

    if style == "matching":
        for j, a in enumerate(answers):
            if not a.get("answer_match_left") or not a.get("answer_match_right"):
                problems.append(f"{where}.answers[{j}]: matching_question needs "
                                "'answer_match_left' and 'answer_match_right'")
            # Matching sides are stored verbatim and shown as plain text, so
            # markup is kept but never rendered -- worse than being dropped.
            for side in ("answer_match_left", "answer_match_right"):
                value = str(a.get(side) or "")
                if MARKUP_RE.search(value):
                    problems.append(
                        f"{where}.answers[{j}]: {side} contains HTML, which "
                        "matching questions store verbatim and display as "
                        "literal text. Use Unicode instead.")

    if style == "numerical":
        for j, a in enumerate(answers):
            kind = a.get("numerical_answer_type")
            if not kind:
                problems.append(f"{where}.answers[{j}]: numerical_question needs "
                                "'numerical_answer_type' (one of "
                                f"{', '.join(sorted(NUMERICAL_REQUIRED))})")
                continue
            if kind not in NUMERICAL_REQUIRED:
                problems.append(f"{where}.answers[{j}]: unknown "
                                f"numerical_answer_type '{kind}'")
                continue
            for field in NUMERICAL_REQUIRED[kind]:
                if field not in a:
                    problems.append(f"{where}.answers[{j}]: {kind} requires "
                                    f"'{field}'")

    return problems


def image_tag(canvas_url, course_id, file_id, alt, width=None):
    """The <img> markup Canvas's own editor produces for a course file.

    The course-scoped /courses/:id/files/:id/preview form is what matters: it
    ties the image to the course, so anyone enrolled can see it. Canvas adds a
    ?verifier= token in the editor, but it is not required here -- Canvas
    rewrites these links per viewer on render.
    """
    base = canvas_url.rstrip("/")
    size = f' width="{width}"' if width else ""
    return (f'<img src="{base}/courses/{course_id}/files/{file_id}/preview"'
            f' alt="{alt}"{size} loading="lazy"'
            f' data-api-endpoint="{base}/api/v1/courses/{course_id}/files/{file_id}"'
            f' data-api-returntype="File">')


def foreign_course_images(text, course_id):
    """Canvas file URLs in `text` that point at a different course.

    Copying a question between semesters carries the old course's file URLs
    with it. The image still renders for the instructor, who can read both
    courses, and is broken for every student in the new one.
    """
    found = set()
    for cid in re.findall(r"/courses/(\d+)/files/\d+", text or ""):
        if str(cid) != str(course_id):
            found.add(cid)
    return sorted(found)


def warn_foreign_images(text, course_id, where):
    foreign = foreign_course_images(text, course_id)
    if foreign:
        print(f"!! {where} embeds an image from course {', '.join(foreign)}, "
              f"not {course_id}. It will look fine to you and be broken for "
              "students. Re-upload the image to this course with the "
              "upload-image subcommand.")


def has_student_submissions(quiz):
    """Whether students have taken this quiz -- without reading their records.

    FERPA: this tool must never fetch student submissions, scores, or
    identities. Canvas's own flags answer the question instead: a quiz that
    students have submitted can no longer be unpublished (`unpublishable` goes
    false), and its assignment reports `has_submitted_submissions`. Neither
    says who submitted or what they scored.
    """
    raw = quiz._requester.request(
        "GET", f"courses/{quiz.course_id}/quizzes/{quiz.id}").json()
    if raw.get("unpublishable") is False:
        return True
    if raw.get("assignment_id"):
        assignment = quiz._requester.request(
            "GET", f"courses/{quiz.course_id}/assignments/"
                   f"{raw['assignment_id']}").json()
        if assignment.get("has_submitted_submissions"):
            return True
    return False


# ---------------------------------------------------------------------------
# Writing questions
#
# Canvas builds the stored question as
#     stored_copy.merge(sent_fields.compact)
# where stored_copy is the question's linked assessment_question
# (canvas-lms AssessmentQuestion.parse_question). Any field that is missing,
# or sent as null, comes from that copy, not from the current quiz question.
# The copy is only refreshed when exactly one quiz question uses it, so a
# quiz copied from an earlier semester keeps that semester's content there
# indefinitely. A partial edit therefore swaps old content back in.
#
# So: every write sends the complete question, every field non-null, built
# from the live question read immediately before writing, and every write is
# read back and compared.
# ---------------------------------------------------------------------------

def question_path(quiz, question_id=None):
    path = f"courses/{quiz.course_id}/quizzes/{quiz.id}/questions"
    return f"{path}/{question_id}" if question_id is not None else path


def get_question_raw(quiz, question_id):
    """The question exactly as Canvas returns it, as a dict."""
    return quiz._requester.request("GET", question_path(quiz, question_id)).json()


def full_question(live):
    """The complete writable form of a question as Canvas has it now."""
    qtype = live.get("question_type")
    q = {"question_type": qtype,
         "points_possible": live.get("points_possible") or 0}
    for field in TEXT_FIELDS:
        q[field] = live.get(field) or ""
    if ANSWER_STYLE.get(qtype) == "matching":
        q["matching_answer_incorrect_matches"] = (
            live.get("matching_answer_incorrect_matches") or "")
    q["answers"] = [canonical_answer(a, qtype, set())
                    for a in (live.get("answers") or [])]
    return q


def fingerprint(live):
    """Short hash of a question's writable content.

    `questions --json` prints it with each question; `edit` refuses to write
    if the live question no longer has the fingerprint the payload was built
    from, i.e. someone else changed it in between.
    """
    blob = json.dumps(full_question(live), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def send_value(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def encode_question(q):
    """Form parameters for a question, answers as an indexed hash.

    canvasapi would encode answers as question[answers][][text], an array
    Rails rebuilds by watching for repeated keys; the indexed form
    question[answers][0][answer_text] is unambiguous. None is never sent:
    Canvas treats null as missing and fills it from the stored copy.
    """
    params = []
    for key in sorted(q):
        if key in ("answers", "position") or q[key] is None:
            continue
        params.append((f"question[{key}]", send_value(q[key])))
    for i, answer in enumerate(q.get("answers") or []):
        for key, value in answer.items():
            if value is None:
                continue
            params.append((f"question[answers][{i}][{key}]", send_value(value)))
    return params


def put_question(quiz, question_id, q):
    return quiz._requester.request("PUT", question_path(quiz, question_id),
                                   _kwargs=encode_question(q)).json()


def post_question(quiz, q):
    return quiz._requester.request("POST", question_path(quiz),
                                   _kwargs=encode_question(q)).json()


def html_signature(text):
    """What matters in a piece of HTML, ignoring how Canvas re-serializes it.

    Canvas sanitizes HTML on save and rewrites file links (adding ?verifier=,
    dropping the host), so an exact comparison would fail on every image. The
    visible text plus the image paths is what a stale-content swap changes.
    """
    plain = html_module.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    images = [urllib.parse.urlparse(html_module.unescape(src)).path
              for src in re.findall(r"<img\b[^>]*?\bsrc=[\"']([^\"']*)",
                                    text or "", flags=re.I)]
    return " ".join(plain.split()), images


def same_html(a, b):
    return (a or "") == (b or "") or html_signature(a) == html_signature(b)


def plain(value):
    return " ".join(str("" if value is None else value).split())


def close(a, b):
    try:
        return abs(float(a or 0) - float(b or 0)) < 1e-9
    except (TypeError, ValueError):
        return plain(a) == plain(b)


NUMERIC_ANSWER_FIELDS = {"answer_weight", "answer_exact", "answer_error_margin",
                         "answer_range_start", "answer_range_end",
                         "answer_approximate", "answer_precision"}
HTML_ANSWER_FIELDS = {"answer_html", "answer_comment_html",
                      "answer_match_left_html"}


def differences(intended, actual):
    """Every way the question Canvas stored differs from the one sent."""
    diffs = []

    def short(value):
        s = plain(value)
        return repr(s if len(s) <= 60 else s[:57] + "...")

    def check(label, want, got, how):
        if not how(want, got):
            diffs.append(f"{label}: sent {short(want)}, Canvas has {short(got)}")

    qtype = intended.get("question_type")
    check("question_type", qtype, actual.get("question_type"),
          lambda a, b: a == b)
    if plain(intended.get("question_name")):
        check("question_name", intended.get("question_name"),
              actual.get("question_name"), lambda a, b: plain(a) == plain(b))
    if qtype != "text_only_question":
        check("points_possible", intended.get("points_possible"),
              actual.get("points_possible"), close)
    check("question_text", intended.get("question_text"),
          actual.get("question_text"), same_html)
    for field in TEXT_FIELDS[2:] + ("matching_answer_incorrect_matches",):
        if field in intended and field in actual:
            how = same_html if field.endswith("_html") else (
                lambda a, b: plain(a) == plain(b))
            check(field, intended[field], actual[field], how)

    want = intended.get("answers") or []
    got = actual.get("answers") or []
    if ANSWER_STYLE.get(qtype) in ("none", "free") and not want:
        return diffs
    if len(want) != len(got):
        diffs.append(f"answers: sent {len(want)}, Canvas has {len(got)}")
        return diffs
    for i, (w, g) in enumerate(zip(want, got)):
        for key, value in w.items():
            if key in ("id", "match_id"):
                continue
            if key == "answer_weight" and qtype in ("short_answer_question",
                                                    "numerical_question"):
                continue    # Canvas forces these to 100
            read_key = ANSWER_WRITE_TO_READ.get(key, key)
            if key in NUMERIC_ANSWER_FIELDS:
                how = close
            elif key in HTML_ANSWER_FIELDS:
                how = same_html
            else:
                how = lambda a, b: plain(a) == plain(b)
            check(f"answers[{i}].{read_key}", value, g.get(read_key), how)
    return diffs


def backup_question(live):
    """Save the question as it was before a write, and return the path."""
    folder = get_datadir() / "bulk_canvas_actions" / "quiz_backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"quiz{live.get('quiz_id')}_q{live.get('id')}_{stamp}.json"
    path.write_text(json.dumps(live, indent=2, default=str))
    return path


def check_writable(quiz, force):
    """Refuse to modify a quiz that students have already taken."""
    taken = has_student_submissions(quiz)
    if taken and not force:
        die(f"quiz '{quiz.title}' has student submissions. Editing it will leave "
            "already-graded attempts inconsistent with the new questions. "
            "Re-run with --force if that is what you want.")
    if taken:
        print(f"!! WARNING: modifying '{quiz.title}', which has student submissions")
    return taken


def warn_if_new_quizzes(course):
    """New Quizzes cannot be edited through this tool; say so plainly."""
    try:
        lti = [a for a in course.get_assignments()
               if getattr(a, "is_quiz_lti_assignment", False)]
    except Exception:
        return
    if lti:
        print(f"note: this course has {len(lti)} New Quizzes assignment(s), which this "
              "tool cannot edit. Only Classic Quizzes are listed below.", file=sys.stderr)


def summarize(question):
    """One-line summary of a question object from the API."""
    return strip_html(question.question_text)[:70]


def cmd_quizzes(course, args):
    warn_if_new_quizzes(course)
    quizzes = sorted(course.get_quizzes(), key=lambda q: (q.title or "").lower())
    print(f"{len(quizzes)} classic quiz(zes) in {course.name}\n")
    print(f"{'id':10} {'q':>3} {'pts':>6} {'pub':>4}  title")
    for q in quizzes:
        print(f"{q.id:<10} {getattr(q, 'question_count', '?'):>3} "
              f"{str(getattr(q, 'points_possible', '?')):>6} "
              f"{'Y' if getattr(q, 'published', False) else 'N':>4}  {q.title}")


def display_order(questions):
    """Questions in the order Canvas shows them, with Canvas's own numbering.

    Canvas does not number text_only_question items, so its "Question 7" is
    not the 7th item on the quiz. That part is confirmed: a quiz with 14 items
    including one text-only reports question_count 13.

    Order is the order the questions endpoint returns, which is by id. Do NOT
    re-sort by position: editing a question nulls its position while leaving
    it displayed in place, so sorting nulls to the end invents a reordering
    that Canvas is not doing. Verified on a live quiz, where two edited
    questions have null positions and still render in their original slots.

    Returns a list of (canvas_number_or_None, question) and a flag saying
    whether any non-null positions contradict this order.
    """
    rows, n = [], 0
    for q in questions:
        if q.question_type == "text_only_question":
            rows.append((None, q))          # Canvas shows these unnumbered
        else:
            n += 1
            rows.append((n, q))

    # If the questions that do carry positions are not in increasing order,
    # the id order and Canvas's order may genuinely disagree.
    known = [q.position for q in questions if q.position is not None]
    consistent = known == sorted(known)
    return rows, consistent


def cmd_questions(course, args):
    quiz = course.get_quiz(args.quiz_id)
    questions = list(quiz.get_questions())
    rows, consistent = display_order(questions)
    taken = has_student_submissions(quiz)
    print(f"{quiz.title}  (id {quiz.id}, {len(questions)} items, "
          f"{getattr(quiz, 'points_possible', '?')} pts, "
          f"{'published' if quiz.published else 'unpublished'}, "
          f"{'HAS student submissions' if taken else 'no student submissions'})")

    if not consistent:
        print("\n  !! stored positions are out of order relative to the order "
              "this endpoint returns;")
        print("  !! the Q# below may not match Canvas. Verify in the web UI.")
    print()

    if args.json:
        payload = []
        for _, q in rows:
            d = {k: v for k, v in q.__dict__.items() if not k.startswith("_")}
            # Keep this in an edited dump: `edit` uses it to refuse a write
            # if someone else changed the question in the meantime.
            d["_fingerprint"] = fingerprint(d)
            payload.append(d)
        print(json.dumps(payload, indent=2, default=str))
        return

    print("  Q#   is the number Canvas shows; text-only items are unnumbered.")
    print(f"  {'Q#':>4}  {'id':>10}  {'pos':>4}  type / points / name")
    for number, q in rows:
        label = f"Q{number}" if number else "--"
        print(f"  {label:>4}  {q.id:>10}  {str(q.position):>4}  "
              f"{q.question_type}  {q.points_possible} pt  {q.question_name!r}")
        print(f"        {summarize(q)}")


def cmd_add(course, args):
    questions = [prepare(q, f"question[{i}]")
                 for i, q in enumerate(load_questions(args.file))]

    problems = []
    for i, q in enumerate(questions):
        problems.extend(validate(q, i))
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        die(f"{len(problems)} problem(s) in payload; nothing was sent to Canvas")

    for i, q in enumerate(questions):
        warn_foreign_images(q.get("question_text"), course.id, f"question[{i}]")

    untested = sorted({q["question_type"] for q in questions
                       if q["question_type"] not in TESTED_TYPES})
    if untested:
        print(f"note: {', '.join(untested)} has never been round-tripped "
              "against Canvas by this tool; check the result in the web UI.")

    quiz = course.get_quiz(args.quiz_id)
    check_writable(quiz, args.force)

    # Questions created through the API come back with position == null, and
    # Canvas ignores a position sent on create (the /reorder endpoint returns
    # 204 but changes nothing). New questions land at the end, in the order
    # they are sent. Reordering has to be done by dragging in the web UI.
    print(f"Adding {len(questions)} question(s) to '{quiz.title}' (id {quiz.id})")
    if args.dry_run:
        print("*** DRY RUN: nothing will be created ***")

    added = mismatched = 0
    for i, q in enumerate(questions):
        label = q.get("question_name") or f"{q['question_type']} #{i + 1}"
        if args.dry_run:
            print(f"  (dry run) would add: {label}  [{q['question_type']}, "
                  f"{q.get('points_possible', 'default')} pt]")
            added += 1
            continue
        # Send every text field, "" if empty, so nothing is left for Canvas
        # to default or fill in.
        for field in TEXT_FIELDS:
            q.setdefault(field, "")
        q.setdefault("points_possible", 0)
        try:
            created = post_question(quiz, q)
        except Exception as e:
            print(f"  ✗ failed to add {label}: {e}")
            continue
        added += 1
        diffs = differences(q, get_question_raw(quiz, created["id"]))
        if diffs:
            mismatched += 1
            print(f"  ✗ added id={created['id']}  {label}, but Canvas stored "
                  "something different:")
            for d in diffs:
                print(f"      {d}")
        else:
            print(f"  ✓ added id={created['id']}  {label}  (read back, matches)")

    if added and not args.dry_run:
        resave(quiz)
    print(f"\n{added}/{len(questions)} question(s) "
          f"{'would be added' if args.dry_run else 'added'}")
    if mismatched:
        die(f"{mismatched} added question(s) do not match what was sent; "
            "check them in Canvas")


def cmd_create(course, args):
    """Create a new quiz. Unpublished unless --publish is given."""
    quiz_data = {
        "title": args.title,
        "quiz_type": args.type,
        "published": args.publish,
        "shuffle_answers": args.shuffle,
        "one_question_at_a_time": args.one_at_a_time,
        "allowed_attempts": args.attempts,
    }
    if args.description:
        quiz_data["description"] = args.description
    if args.time_limit:
        quiz_data["time_limit"] = args.time_limit
    if args.access_code:
        quiz_data["access_code"] = args.access_code
    for field, value in (("due_at", args.due), ("unlock_at", args.unlock),
                         ("lock_at", args.lock)):
        if value is not None:
            quiz_data[field] = value

    if args.group:
        group_id = find_assignment_group_id(course, args.group)
        if group_id is None:
            die(f"no assignment group named '{args.group}' in this course")
        quiz_data["assignment_group_id"] = group_id

    print(f"Creating quiz in '{course.name}':")
    for k, v in quiz_data.items():
        shown = show_when(v) if k.endswith("_at") else v
        print(f"  {k} = {shown}")
    if args.attempts == -1:
        print("  (allowed_attempts -1 means unlimited)")
    if not args.publish:
        print("  NOTE: unpublished, so students cannot see it yet. "
              "A file_upload question cannot be tested until it is published.")

    if args.dry_run:
        print("*** DRY RUN: nothing was created ***")
        return

    quiz = course.create_quiz(quiz=quiz_data)
    print(f"\n  ✓ created quiz {quiz.id}")
    print(f"  {CANVAS_URL}/courses/{course.id}/quizzes/{quiz.id}")
    print(f"\nAdd questions with:\n  quiz_tool.py --course-id {course.id} "
          f"add {quiz.id} --file questions.json")


def find_assignment_group_id(course, name):
    """id of the assignment group with this name, or None."""
    for group in course.get_assignment_groups():
        if group.name.strip().lower() == name.strip().lower():
            return group.id
    return None


def cmd_find(course, args):
    """Search question text (and optionally answers) across a course."""
    try:
        pattern = re.compile(args.pattern if args.regex else re.escape(args.pattern),
                             0 if args.case_sensitive else re.IGNORECASE)
    except re.error as e:
        die(f"bad regex: {e}")

    quizzes = list(course.get_quizzes())
    if args.quiz:
        quizzes = [q for q in quizzes if q.id == args.quiz]
        if not quizzes:
            die(f"quiz {args.quiz} not found in this course")

    print(f"Searching {len(quizzes)} quiz(zes) in '{course.name}' for "
          f"{args.pattern!r}\n")
    hits = scanned = 0
    for quiz in quizzes:
        try:
            questions = list(quiz.get_questions())
        except Exception as e:
            print(f"  (skipped '{quiz.title}': {e})")
            continue

        matches, number = [], 0
        for q in questions:
            if q.question_type != "text_only_question":
                number += 1
            scanned += 1
            if args.type and q.question_type != args.type:
                continue
            body = strip_html(q.question_text)
            answer_text = " ".join(
                str(a.get("text") or a.get("left") or "") for a in (q.answers or []))
            # Track where the match landed so the output can say so honestly.
            where = None
            if pattern.search(body):
                where = "body"
            elif pattern.search(q.question_name or ""):
                where = "name"
            elif args.answers and pattern.search(answer_text):
                where = "answers"
            if where:
                label = f"Q{number}" if q.question_type != "text_only_question" else "--"
                matches.append((label, q, body, where, answer_text))

        if matches:
            print(f"  {quiz.title}  (quiz {quiz.id}, "
                  f"{'published' if quiz.published else 'unpublished'})")
            for label, q, body, where, answer_text in matches:
                print(f"    {label:>4}  id={q.id}  {q.question_type}  "
                      f"{q.points_possible} pt   [match in {where}]")
                source = {"body": body, "name": q.question_name or "",
                          "answers": answer_text}[where]
                m = pattern.search(source)
                start = max(0, m.start() - 35)
                snippet = source[start:m.end() + 35]
                print(f"          …{snippet}…" if start else f"          {snippet}…")
                hits += 1
            print()

    print(f"{hits} match(es) across {scanned} question(s).")


def cmd_settings(course, args):
    """Show quiz settings, or change the ones given as flags."""
    quiz = course.get_quiz(args.quiz_id)
    raw = quiz._requester.request(
        "GET", f"courses/{course.id}/quizzes/{quiz.id}").json()

    changes = {}
    for field in QUIZ_SETTINGS:
        value = getattr(args, field, None)
        if value is not None:
            changes[field] = value

    if not changes:
        print(f"{raw.get('title')}  (quiz {quiz.id})")
        print(f"  {CANVAS_URL}/courses/{course.id}/quizzes/{quiz.id}\n")
        for field, kind in QUIZ_SETTINGS.items():
            value = raw.get(field)
            shown = show_when(value) if kind == "date" else value
            if field == "allowed_attempts" and value == -1:
                shown = "-1 (unlimited)"
            print(f"  {field:24} {shown}")
        print(f"\n  {'student submissions':24} "
              f"{'yes' if has_student_submissions(quiz) else 'no'}")
        print(f"  {'question_count':24} {raw.get('question_count')}")
        return

    subs = has_student_submissions(quiz)
    risky = sorted(set(changes) & RISKY_WITH_SUBMISSIONS)
    if subs and risky and not args.force:
        die(f"quiz '{raw.get('title')}' has student submissions, and "
            f"{', '.join(risky)} change(s) the conditions students were graded "
            "under. Re-run with --force if that is intended. Date and title "
            "changes do not need --force.")
    if subs:
        print(f"!! this quiz has student submissions; changing settings now "
              "affects a quiz students have already taken")

    print(f"Updating '{raw.get('title')}' (quiz {quiz.id}):")
    for field, value in changes.items():
        kind = QUIZ_SETTINGS[field]
        before = show_when(raw.get(field)) if kind == "date" else raw.get(field)
        after = show_when(value) if kind == "date" else value
        print(f"  {field}: {before!r} -> {after!r}")

    if args.dry_run:
        print("*** DRY RUN: nothing was changed ***")
        return

    payload = dict(changes)
    payload["notify_of_update"] = args.notify
    try:
        quiz.edit(quiz=payload)
    except Exception as e:
        die(f"failed to update quiz: {e}")
    after = quiz._requester.request(
        "GET", f"courses/{course.id}/quizzes/{quiz.id}").json()
    print("  ✓ updated")
    for field in changes:
        kind = QUIZ_SETTINGS[field]
        value = after.get(field)
        print(f"    {field} is now "
              f"{show_when(value) if kind == 'date' else value!r}")


def equation_tag(latex, canvas_url=None):
    """Canvas's server-rendered LaTeX markup for one formula.

    The src path is the LaTeX percent-encoded TWICE, which is what Canvas's own
    editor produces and what its endpoint expects.
    """
    base = (canvas_url or CANVAS_URL).rstrip("/")
    encoded = urllib.parse.quote(urllib.parse.quote(latex, safe=""), safe="")
    escaped = html_module.escape(latex, quote=True)
    return (f'<img class="equation_image" title="{escaped}" '
            f'src="{base}/equation_images/{encoded}" '
            f'alt="LaTeX: {escaped}" data-equation-content="{escaped}" '
            f'loading="lazy">')


def cmd_equation(course, args):
    """Print the markup for a LaTeX formula, ready to paste into a question."""
    for latex in args.latex:
        print(equation_tag(latex))


def cmd_upload_image(course, args):
    """Upload a local image into the course's files and print its <img> tag."""
    path = pathlib.Path(args.path).expanduser()
    if not path.is_file():
        die(f"no such file: {path}")

    alt = args.alt or path.stem.replace("_", " ")
    width = args.width
    if width is None:
        try:
            from PIL import Image
            with Image.open(path) as im:
                # Keep wide images from overflowing the question column.
                width = min(im.width, 700)
        except Exception:
            pass

    print(f"Uploading {path.name} ({path.stat().st_size:,} bytes) to "
          f"'{course.name}' / {args.folder}")
    if args.dry_run:
        print("*** DRY RUN: nothing was uploaded ***")
        print(image_tag(CANVAS_URL, course.id, "<FILE_ID>", alt, width))
        return

    ok, response = course.upload(str(path), parent_folder_path=args.folder)
    if not ok:
        die(f"upload failed: {response}")
    file_id = response["id"]
    print(f"  ✓ uploaded as file {file_id} ({response['display_name']})")
    print("\nPaste this into a question's question_text:\n")
    print(image_tag(CANVAS_URL, course.id, file_id, alt, width))


def cmd_edit(course, args):
    """Change some fields of a question, always writing the whole question.

    The payload may name only the fields to change, but what is sent is the
    live question with those fields replaced -- never a partial update, which
    Canvas would complete from a possibly stale stored copy.
    """
    payload = load_questions(args.file)
    if len(payload) != 1:
        die("edit takes exactly one question object")
    changes = payload[0]
    expected = args.expect or changes.get("_fingerprint")

    quiz = course.get_quiz(args.quiz_id)
    live = get_question_raw(quiz, args.question_id)
    base_fp = fingerprint(live)
    if expected and expected != base_fp:
        die(f"question {args.question_id} has changed since the copy your edit "
            f"was built from (fingerprint {expected}, now {base_fp}). Someone "
            "else may be editing it. Re-read it with `questions --json`, "
            "redo the edit on the current version, and try again.")

    base = full_question(live)
    merged = json.loads(json.dumps(base))
    # Changing a plain comment without its _html twin would leave the old
    # html, which Canvas displays in preference to the plain text.
    for field in COMMENT_FIELDS:
        if field in changes and f"{field}_html" not in changes:
            merged[f"{field}_html"] = ""
    merged.update({k: v for k, v in changes.items() if k != "_fingerprint"})
    for field in TEXT_FIELDS:
        if merged.get(field) is None:
            merged[field] = ""
    if merged.get("points_possible") is None:
        merged["points_possible"] = 0
    # Re-canonicalize all answers against the final type, in case the edit
    # changed the type but kept the answers.
    prepare(merged, "payload")
    # Canvas compares the sent group id (a string) with the stored one (an
    # integer), so re-sending the current group counts as a move and resets
    # the question's position. Send it only when it actually changes.
    if str(merged.get("quiz_group_id")) == str(live.get("quiz_group_id")):
        merged.pop("quiz_group_id", None)
    if (ANSWER_STYLE.get(merged.get("question_type")) == "matching"
            and merged.get("matching_answer_incorrect_matches") is None):
        merged["matching_answer_incorrect_matches"] = ""

    problems = validate(merged, 0)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        die(f"{len(problems)} problem(s) in the edited question; nothing was "
            "sent to Canvas")
    warn_foreign_images(merged.get("question_text"), course.id, "question_text")

    check_writable(quiz, args.force)

    print(f"Editing question {live.get('id')} ({live.get('question_type')}) "
          f"in '{quiz.title}'")
    print(f"  fingerprint {base_fp}")
    changed = [f for f in sorted(set(base) | set(merged))
               if base.get(f) != merged.get(f)]
    if not changed:
        print("  no field changes; the full question will be rewritten as-is")
    for field in changed:
        before, after = base.get(field), merged.get(field)
        if field == "answers":
            print(f"  answers: {len(before or [])} -> {len(after or [])} "
                  "(full list replaced)")
            for i, a in enumerate(after or []):
                old = before[i] if i < len(before or []) else None
                if a != old:
                    print(f"    [{i}] {json.dumps(a, ensure_ascii=False)}")
        else:
            print(f"  {field}: {before!r} -> {after!r}")
    if live.get("assessment_question_id"):
        print(f"  note: linked to stored copy {live['assessment_question_id']}; "
              "sending every field so none of it is used")

    if args.dry_run:
        print("*** DRY RUN: nothing was changed ***")
        print(f"To guard against concurrent edits, re-run with --expect {base_fp}")
        return

    # Re-read right before writing: the submission check and the user's
    # review can take a while, and a shared course can change underneath.
    current = get_question_raw(quiz, args.question_id)
    if fingerprint(current) != base_fp:
        die("the question changed while this edit was being prepared; "
            "nothing was sent. Re-read it and try again.")
    backup = backup_question(current)

    try:
        put_question(quiz, args.question_id, merged)
    except Exception as e:
        die(f"failed to edit question: {e}  (previous version saved to {backup})")

    after = get_question_raw(quiz, args.question_id)
    diffs = differences(merged, after)
    if diffs:
        print("  ✗ Canvas stored something different from what was sent:",
              file=sys.stderr)
        for d in diffs:
            print(f"      {d}", file=sys.stderr)
        die("the question in Canvas does NOT match the edit. Check it in the "
            f"web UI now. The previous version is saved at {backup}")
    print(f"  ✓ updated and read back: matches (fingerprint {fingerprint(after)})")
    print(f"  previous version saved to {backup}")
    resave(quiz)


def cmd_delete(course, args):
    quiz = course.get_quiz(args.quiz_id)
    question = quiz.get_question(args.question_id)
    check_writable(quiz, args.force)

    print(f"Deleting question {question.id} ({question.question_type}, "
          f"{question.points_possible} pt) from '{quiz.title}'")
    print(f"  {summarize(question)}")

    if args.dry_run:
        print("*** DRY RUN: nothing was deleted ***")
        return
    if not args.yes:
        if input("Proceed? (yes/no): ").strip().lower() != "yes":
            print("Cancelled.")
            return
    question.delete()
    print("  ✓ deleted")
    resave(quiz)


def settle(fetch, expected, attempts=4, delay=0.5):
    """Poll `fetch` until Canvas's cached question count catches up.

    question_count trails a write by a moment and then settles on its own, so
    the first read after a write is routinely stale. Only a count that never
    catches up means anything.
    """
    result = fetch()
    for _ in range(attempts - 1):
        if result[0] == expected:
            break
        time.sleep(delay)
        result = fetch()
    return result


def resave(quiz):
    """Make Canvas recompute the quiz totals after questions change.

    A published quiz serves a frozen snapshot (quiz_data). Adding or removing
    questions through the API leaves that snapshot stale: question_count and
    points_possible stay at their old values and students can be served the
    old question set. A plain edit does NOT clear it -- only an unpublish /
    republish cycle does. Verified against Canvas.

    Unpublishing is refused by Canvas once a quiz has submissions, so in that
    case say what is wrong instead of trying.

    On an UNPUBLISHED quiz a stale count is cosmetic: nothing is being served.
    Do not warn about it, or the warning becomes noise that hides the real one.
    """
    try:
        quiz.edit(quiz={"notify_of_update": False})
        questions = list(quiz.get_questions())
        items = len(questions)
        # Canvas's question_count excludes text-only items.
        graded = sum(1 for q in questions
                     if q.question_type != "text_only_question")

        def totals():
            r = quiz._requester.request(
                "GET", f"courses/{quiz.course_id}/quizzes/{quiz.id}").json()
            return r.get("question_count"), r.get("points_possible"), r.get("published")

        count, points, published = settle(totals, graded)

        if published and count != graded:
            if has_student_submissions(quiz):
                print(f"  !! this published quiz still serves {count} question(s) "
                      f"but has {graded}. Republishing would fix it, but the quiz "
                      "has student submissions, so Canvas will not let it be "
                      "unpublished. Open it in Canvas and save it there.")
            else:
                quiz.edit(quiz={"published": False})
                quiz.edit(quiz={"published": True, "notify_of_update": False})
                count, points, published = settle(totals, graded)
                print("  (republished to refresh the quiz's cached question list)")

        line = f"  quiz now has {items} item(s), {points} pts"
        if published and count != graded:
            line += (f"  !! Canvas still serves {count}; "
                     "open the quiz in Canvas and save it")
        print(line)
    except Exception as e:
        print(f"  note: could not refresh quiz totals ({e}); "
              "open the quiz in Canvas and click Save if counts look stale")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect and edit questions on Canvas Classic Quizzes.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("quizzes", help="list classic quizzes in the course")
    p.set_defaults(func=cmd_quizzes)

    p = sub.add_parser("questions", help="list questions on one quiz")
    p.add_argument("quiz_id", type=int)
    p.add_argument("--json", action="store_true",
                   help="dump full question payloads as JSON")
    p.set_defaults(func=cmd_questions)

    p = sub.add_parser("add", help="add question(s) to a quiz from a JSON file")
    p.add_argument("quiz_id", type=int)
    p.add_argument("--file", required=True,
                   help="JSON file with a question object or list ('-' for stdin)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("edit", help="update one existing question")
    p.add_argument("quiz_id", type=int)
    p.add_argument("question_id", type=int)
    p.add_argument("--file", required=True,
                   help="JSON file with the fields to change ('-' for stdin). "
                        "The whole question is always written: these fields "
                        "replace the live ones and the rest are re-sent as-is.")
    p.add_argument("--expect", metavar="FINGERPRINT",
                   help="refuse to write unless the live question still has this "
                        "fingerprint (printed by --dry-run and `questions --json`; "
                        "a _fingerprint field in the payload does the same)")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("create", help="create a new quiz")
    p.add_argument("title")
    p.add_argument("--type", default="assignment",
                   choices=["assignment", "practice_quiz", "graded_survey", "survey"],
                   help="quiz type (default: assignment, i.e. a graded quiz)")
    p.add_argument("--description", help="HTML shown above the questions")
    p.add_argument("--due", type=parse_when, metavar="DATE",
                   help="due date, e.g. '12/10/2026' (11:59 PM) or '12/10/2026 17:00'")
    p.add_argument("--unlock", type=parse_when, metavar="DATE",
                   help="date the quiz becomes available")
    p.add_argument("--lock", type=parse_when, metavar="DATE",
                   help="date the quiz closes")
    p.add_argument("--time-limit", type=int, metavar="MIN", help="time limit in minutes")
    p.add_argument("--attempts", type=int, default=1,
                   help="allowed attempts; -1 for unlimited (default: 1)")
    p.add_argument("--shuffle", action="store_true",
                   help="shuffle answers (recommended if the quiz uses dropdown blanks)")
    p.add_argument("--one-at-a-time", action="store_true",
                   help="show one question at a time (breaks file_upload questions)")
    p.add_argument("--access-code", help="require this code to take the quiz")
    p.add_argument("--group", metavar="NAME", help="assignment group to file it under")
    p.add_argument("--publish", action="store_true",
                   help="publish immediately (default: unpublished)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be created without creating it")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("find", help="search questions across the course")
    p.add_argument("pattern", help="text to search for")
    p.add_argument("--regex", action="store_true", help="treat pattern as a regex")
    p.add_argument("--case-sensitive", action="store_true")
    p.add_argument("--answers", action="store_true", help="search answer text too")
    p.add_argument("--type", help="only this question_type")
    p.add_argument("--quiz", type=int, metavar="QUIZ_ID",
                   help="search only this quiz")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("settings",
                       help="show quiz settings, or change them with flags")
    p.add_argument("quiz_id", type=int)
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--quiz-type", dest="quiz_type",
                   choices=["assignment", "practice_quiz", "graded_survey", "survey"])
    p.add_argument("--due", dest="due_at", type=parse_when, metavar="DATE",
                   help="e.g. '12/10/2026' (11:59 PM), '12/10/2026 17:00', or 'none'")
    p.add_argument("--unlock", dest="unlock_at", type=parse_when, metavar="DATE")
    p.add_argument("--lock", dest="lock_at", type=parse_when, metavar="DATE")
    p.add_argument("--time-limit", dest="time_limit", type=int, metavar="MIN")
    p.add_argument("--attempts", dest="allowed_attempts", type=int,
                   help="-1 for unlimited")
    p.add_argument("--scoring-policy", dest="scoring_policy",
                   choices=["keep_highest", "keep_latest"])
    p.add_argument("--access-code", dest="access_code")
    for flag, dest, helptext in (
            ("shuffle", "shuffle_answers", "shuffle answer order"),
            ("one-at-a-time", "one_question_at_a_time", "show one question at a time"),
            ("cant-go-back", "cant_go_back", "lock each question after answering"),
            ("show-correct-answers", "show_correct_answers", "show correct answers after submitting"),
            ("published", "published", "publish or unpublish the quiz")):
        g = p.add_mutually_exclusive_group()
        g.add_argument(f"--{flag}", dest=dest, action="store_true", default=None,
                       help=helptext)
        g.add_argument(f"--no-{flag}", dest=dest, action="store_false", default=None,
                       help=f"turn off: {helptext}")
    p.add_argument("--notify", action="store_true",
                   help="notify students of the change (default: do not)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="allow risky changes on a quiz with submissions")
    p.set_defaults(func=cmd_settings)

    p = sub.add_parser("equation",
                       help="print Canvas LaTeX markup for a formula")
    p.add_argument("latex", nargs="+",
                   help=r"LaTeX source, e.g. 'O(n^2)' or '\Theta(n \log n)'")
    p.set_defaults(func=cmd_equation)

    p = sub.add_parser("upload-image",
                       help="upload an image to the course and print its <img> tag")
    p.add_argument("path", help="local image file")
    p.add_argument("--alt", help="alt text (defaults to the filename)")
    p.add_argument("--width", type=int,
                   help="width attribute; defaults to the image width capped at 700")
    p.add_argument("--folder", default="quiz_images",
                   help="course files folder (default: quiz_images)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be uploaded without doing it")
    p.set_defaults(func=cmd_upload_image)

    p = sub.add_parser("delete", help="delete one question")
    p.add_argument("quiz_id", type=int)
    p.add_argument("question_id", type=int)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_delete)

    # Flags shared by the mutating subcommands.
    for name in ("add", "edit", "delete"):
        sp = sub.choices[name]
        sp.add_argument("--dry-run", action="store_true",
                        help="show what would change without sending it to Canvas")
        sp.add_argument("--force", action="store_true",
                        help="allow editing a quiz that already has submissions")

    canvas, course, args = get_canvas_and_course(parser)
    args.func(course, args)


if __name__ == "__main__":
    main()
