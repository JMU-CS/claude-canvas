"""Offline tests for quiz_tool.py against a fake Canvas that mimics
AssessmentQuestion.parse_question: missing/null fields come from a stale
stored copy, and each type's answer parser reads only the names it reads."""
import copy, io, json, os, re, sys, tempfile, contextlib, types
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "plugins/jmu-canvas/scripts"))
import quiz_tool as qt

READS = {  # per-type: write-name -> read-name that the Canvas parser honours
    "multiple_choice_question": {"answer_text": "text", "text": "text", "answer_weight": "weight", "weight": "weight", "answer_html": "html", "html": "html", "id": "id"},
    "true_false_question": {"answer_text": "text", "text": "text", "answer_weight": "weight", "weight": "weight", "id": "id"},
    "multiple_dropdowns_question": {"answer_text": "text", "text": "text", "answer_weight": "weight", "blank_id": "blank_id", "id": "id"},
    "matching_question": {"answer_match_left": "left", "answer_match_right": "right", "match_id": "match_id", "id": "id"},
    "numerical_question": {"numerical_answer_type": "numerical_answer_type", "answer_exact": "exact", "answer_error_margin": "margin"},
}
SLICE = ["points_possible", "correct_comments", "incorrect_comments", "neutral_comments",
         "question_type", "question_name", "question_text", "answers",
         "matching_answer_incorrect_matches", "correct_comments_html",
         "incorrect_comments_html", "neutral_comments_html"]


def parse_form(params):
    """Rebuild Rails params from encoded pairs (indexed hash form only)."""
    q, answers = {}, {}
    for k, v in params:
        m = re.fullmatch(r"question\[answers\]\[(\d+)\]\[(\w+)\]", k)
        if m:
            answers.setdefault(int(m[1]), {})[m[2]] = v
            continue
        m = re.fullmatch(r"question\[(\w+)\]", k)
        assert m, f"unexpected param {k}"
        q[m[1]] = v
    if answers:
        q["answers"] = [answers[i] for i in sorted(answers)]
    return q


class FakeCanvas:
    def __init__(self, live, stale):
        self.live = live          # dict as the read API returns it
        self.stale = stale        # stored assessment_question copy
        self.puts = []

    def request(self, method, path, _kwargs=None, **kw):
        if method == "GET":
            return types.SimpleNamespace(json=lambda: copy.deepcopy(self.live))
        sent = parse_form(_kwargs)
        self.puts.append(sent)
        data = {**self.stale, **{k: v for k, v in sent.items() if v is not None}}
        data = {k: data[k] for k in SLICE if k in data}
        qtype = data["question_type"]
        reads = READS[qtype]
        out_answers = []
        for a in data.get("answers", []):
            r = {}
            for k, v in a.items():
                if k in reads:
                    r.setdefault(reads[k], v)
            if qtype == "multiple_dropdowns_question":
                r["weight"] = float(a.get("answer_weight", 0))
            out_answers.append(r)
        if qtype == "multiple_dropdowns_question":
            seen = {}
            for r in out_answers:
                seen.setdefault(r["blank_id"], False)
                seen[r["blank_id"]] |= r["weight"] == 100
            for b, ok in seen.items():
                if not ok:
                    next(r for r in out_answers if r["blank_id"] == b)["weight"] = 100.0
        data["answers"] = out_answers
        data["points_possible"] = float(data.get("points_possible") or 0)
        self.live = {**self.live, **data}
        return types.SimpleNamespace(json=lambda: copy.deepcopy(self.live))


def run_edit(fake, changes, expect=None, dry=False):
    quiz = types.SimpleNamespace(course_id=1, id=2, title="Quiz", _requester=fake)
    course = types.SimpleNamespace(id=1, get_quiz=lambda _id: quiz)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(changes, f)
    args = types.SimpleNamespace(file=f.name, quiz_id=2, question_id=fake.live["id"],
                                 expect=expect, dry_run=dry, force=False, republish=False)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            qt.cmd_edit(course, args)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


real_check_writable = qt.check_writable
qt.check_writable = lambda quiz, force: 0
real_resave = qt.resave
qt.resave = lambda quiz, republish=False: None
qt.get_datadir = lambda: __import__("pathlib").Path(tempfile.mkdtemp())

TF_LIVE = {"id": 10, "quiz_id": 2, "question_type": "true_false_question",
           "question_name": "Question", "question_text": "<p>Heapsort is stable.</p>",
           "points_possible": 0.5, "correct_comments": "", "incorrect_comments": "",
           "neutral_comments": "", "correct_comments_html": None,
           "incorrect_comments_html": None, "neutral_comments_html": None,
           "assessment_question_id": 99,
           "answers": [{"id": 1, "text": "True", "weight": 0, "comments": "", "comments_html": ""},
                       {"id": 2, "text": "False", "weight": 100, "comments": "", "comments_html": ""}]}
STALE = {"question_type": "multiple_choice_question", "question_name": "Old",
         "question_text": "<p>S26 version</p>", "points_possible": 1.0,
         "correct_comments_html": "<p>stale comment</p>",
         "answers": [{"text": f"opt{i}", "weight": 100 if i == 0 else 0} for i in range(5)]}

failures = 0
def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"\n{detail}"))
    failures += not cond

# 1. Text-only edit must not pull stale type/answers/points/comments.
fake = FakeCanvas(copy.deepcopy(TF_LIVE), STALE)
code, out, err = run_edit(fake, {"question_text": "<p>Heapsort is not stable.</p>"})
sent = fake.puts[-1] if fake.puts else {}
check("text-only edit succeeds", code == 0, out + err)
check("full question sent", all(k in sent for k in ["question_type", "question_name", "points_possible", "answers", "correct_comments_html"]), sent)
check("type/points/answers preserved", fake.live["question_type"] == "true_false_question" and fake.live["points_possible"] == 0.5 and len(fake.live["answers"]) == 2, fake.live)
check("stale comment not restored", fake.live.get("correct_comments_html") == "", fake.live.get("correct_comments_html"))
check("answer ids kept", [a.get("id") for a in sent["answers"]] == [1, 2], sent["answers"])

# 2. Answers-only edit with 5 answers on T/F is refused before sending.
fake = FakeCanvas(copy.deepcopy(TF_LIVE), STALE)
code, out, err = run_edit(fake, {"answers": [{"text": t, "weight": 100 if i == 0 else 0}
                                             for i, t in enumerate(["True", "False"] * 2 + ["True"])]})
check("5 answers on T/F refused for the count", code != 0 and not fake.puts and "exactly two" in err, err)

# 3. Dropdown answers written in read names come out as answer_weight; key honoured.
DD = {"id": 11, "quiz_id": 2, "question_type": "multiple_dropdowns_question", "question_name": "Q",
      "question_text": "<p>Merge sort is [s] and runs in [t].</p>", "points_possible": 1,
      "answers": [{"text": "unstable", "weight": 0, "blank_id": "s"}, {"text": "stable", "weight": 100, "blank_id": "s"},
                  {"text": "O(n)", "weight": 0, "blank_id": "t"}, {"text": "O(n log n)", "weight": 100, "blank_id": "t"}]}
fake = FakeCanvas(copy.deepcopy(DD), {**STALE, "question_type": "multiple_dropdowns_question"})
code, out, err = run_edit(fake, {"question_text": "<p>Mergesort is [s] and runs in [t].</p>"})
check("dropdown key preserved (correct not first)", code == 0 and [a["weight"] for a in fake.live["answers"]] == [0, 100, 0, 100], out + err + str(fake.live["answers"]))
check("dropdown sent with answer_weight", all("answer_weight" in a and "weight" not in a for a in fake.puts[-1]["answers"]), fake.puts[-1])

# 4. Fingerprint mismatch refuses.
fake = FakeCanvas(copy.deepcopy(TF_LIVE), STALE)
code, out, err = run_edit(fake, {"points_possible": 1, "_fingerprint": "deadbeef0000"})
check("stale fingerprint refused", code != 0 and not fake.puts, err)
fp = qt.fingerprint(TF_LIVE)
code, out, err = run_edit(fake, {"points_possible": 1}, expect=fp)
check("matching fingerprint accepted", code == 0 and fake.live["points_possible"] == 1.0, out + err)

# 5. A Canvas that mangles the write is caught by the read-back.
class Broken(FakeCanvas):
    def request(self, method, path, _kwargs=None, **kw):
        r = super().request(method, path, _kwargs, **kw)
        if method == "PUT":
            self.live["question_text"] = "<p>S26 version</p>"
        return r
fake = Broken(copy.deepcopy(TF_LIVE), STALE)
code, out, err = run_edit(fake, {"question_text": "<p>New</p>"})
check("mismatch after write fails loudly", code != 0 and "does NOT match" in err and "✓" not in out, out + err)

# 6. A whole --json dump can be edited and sent back.
fake = FakeCanvas(copy.deepcopy(TF_LIVE), STALE)
dump = copy.deepcopy(TF_LIVE); dump["_fingerprint"] = qt.fingerprint(TF_LIVE); dump["position"] = 3
dump["question_text"] = "<p>Edited from dump</p>"
code, out, err = run_edit(fake, dump)
check("dump round-trips", code == 0 and fake.live["question_text"] == "<p>Edited from dump</p>", out + err)

# 7. Verification tolerates Canvas re-serializing image links.
a = '<p>x <img src="https://canvas.jmu.edu/courses/1/files/5/preview" alt="a"></p>'
b = '<p>x <img src="/courses/1/files/5/preview?verifier=abc" alt="a"></p>'
check("same_html ignores verifier/host", qt.same_html(a, b))
check("same_html catches text change", not qt.same_html("<p>new</p>", "<p>old</p>"))

# 8. Matching and numerical canonicalisation.
m = qt.canonical_answer({"left": "A", "right": "B", "text": "A", "match_id": 7, "comments": ""}, "matching_question", set())
check("matching read->write", m == {"answer_match_left": "A", "answer_match_right": "B", "match_id": 7}, m)
n = qt.canonical_answer({"numerical_answer_type": "exact_answer", "exact": 42.0, "margin": 0.0, "start": None, "text": "", "weight": 100}, "numerical_question", set())
check("numerical read->write", n == {"numerical_answer_type": "exact_answer", "answer_exact": 42.0, "answer_error_margin": 0.0, "answer_text": ""}, n)
dropped = set()
qt.canonical_answer({"text": "x", "wieght": 100}, "multiple_choice_question", dropped)
check("typo key reported", dropped == {"wieght"}, dropped)

# 9. Dry run sends nothing and prints the fingerprint.
fake = FakeCanvas(copy.deepcopy(TF_LIVE), STALE)
code, out, err = run_edit(fake, {"points_possible": 2}, dry=True)
check("dry run sends nothing", code == 0 and not fake.puts and f"--expect {qt.fingerprint(TF_LIVE)}" in out, out)

# 10. Formula questions: evaluator and answer-set generation.
check("formula arithmetic", qt.evaluate_formulas(["n*(n-1)/2"], {"n": 10}) == 45)
check("formula power and functions", abs(qt.evaluate_formulas(["2^k + sqrt(x) + log(100) + ln(e)"], {"k": 3, "x": 16}) - 15) < 1e-9)
check("formula intermediate lines", qt.evaluate_formulas(["m = a + b", "m * 2"], {"a": 1, "b": 2}) == 6)
try:
    qt.evaluate_formula("__import__('os').system('true')", {}); ok = False
except ValueError:
    ok = True
check("formula evaluator refuses arbitrary code", ok)
calc = {"question_type": "calculated_question", "question_text": "<p>[n] items</p>",
        "formulas": [{"formula": "n*n"}], "variables": [{"name": "n", "min": 2, "max": 9, "scale": 0}],
        "formula_decimal_places": 0, "answer_count": 5}
qt.prepare(calc)
check("answer sets generated", len(calc["answers"]) == 5 and all(
    a["answer_text"] == int(a["variables"][0]["value"]) ** 2 for a in calc["answers"]), calc["answers"])
check("calculated payload valid", qt.validate(calc, 0) == [], qt.validate(calc, 0))
params = dict(qt.encode_question(calc))
check("nested encoding", "question[formulas][0]" in params and "question[answers][0][variables][0][value]" in params, list(params)[:8])

# 11. resave on an unpublished quiz saves it (title re-sent, never published)
# so Canvas recomputes points_possible and question_count.
class FakeQuiz:
    def __init__(self, published):
        self.id, self.course_id = 7, 3
        self.state = {"title": "Q", "published": published,
                      "question_count": 0, "points_possible": None}
        self.edits = []
        self._requester = self
    def request(self, method, path, **kw):
        assert method == "GET", method
        return types.SimpleNamespace(json=lambda: dict(self.state))
    def get_questions(self):
        return [types.SimpleNamespace(question_type="calculated_question"),
                types.SimpleNamespace(question_type="text_only_question")]
    def edit(self, quiz):
        self.edits.append(quiz)
        self.state.update(quiz)
        self.state.update(question_count=1, points_possible=10.0)  # Canvas recomputes on save
with contextlib.redirect_stdout(io.StringIO()) as out:
    fq = FakeQuiz(published=False)
    real_resave(fq)
check("unpublished quiz saved with its own title only", fq.edits == [{"title": "Q"}], fq.edits)
check("unpublished quiz stays unpublished", fq.state["published"] is False)
check("unpublished resave reports new points", "10.0 pts" in out.getvalue() and "!!" not in out.getvalue(), out.getvalue())

# 12. A dump or backup round-trips: timestamps and canvasapi's *_date copies
# are dropped, not rejected.
dumped = dict(copy.deepcopy(TF_LIVE), created_at="2026-11-11T13:00:00Z",
              created_at_date="2026-11-11 13:00:00+00:00", course_id_date="x",
              _fingerprint="abc")
qt.prepare(dumped)
check("dump fields dropped by prepare", qt.validate(dumped, 0) == [], qt.validate(dumped, 0))

# 13. An edit that changes nothing sends nothing.
fake = FakeCanvas(copy.deepcopy(TF_LIVE), STALE)
code, out, err = run_edit(fake, {"points_possible": TF_LIVE["points_possible"]})
check("no-op edit sends nothing", code in (0, None) and not fake.puts and "nothing was sent" in out, out + err)

# 14. resave on a PUBLISHED quiz: by default it does not toggle (republishing
# notifies students); --republish toggles and must end published.
class FailingPublish(FakeQuiz):
    def edit(self, quiz):
        if quiz.get("published"):
            raise RuntimeError("HTTP 500")
        super().edit(quiz)

real_subs = qt.has_student_submissions
qt.has_student_submissions = lambda quiz: False
qt.time.sleep = lambda s: None
with contextlib.redirect_stdout(io.StringIO()) as out:
    fq = FakeQuiz(published=True)
    real_resave(fq)
check("published quiz not toggled by default", fq.edits == [] and "click Edit, then Save" in out.getvalue(), out.getvalue())
with contextlib.redirect_stdout(io.StringIO()) as out:
    fq = FakeQuiz(published=True)
    real_resave(fq, republish=True)
check("--republish unpublishes then republishes", [e.get("published") for e in fq.edits] == [False, True]
      and fq.state["published"] is True, fq.edits)
fq = FailingPublish(published=True)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
    try:
        real_resave(fq, republish=True); code = 0
    except SystemExit as e:
        code = e.code
check("failed republish exits loudly", code and "UNPUBLISHED" in err.getvalue(), (code, err.getvalue()))
qt.has_student_submissions = lambda quiz: True
with contextlib.redirect_stdout(io.StringIO()) as out:
    fq = FakeQuiz(published=True)
    real_resave(fq, republish=True)
check("quiz with submissions never toggled", fq.edits == [] and "cannot be republished" in out.getvalue(), out.getvalue())
qt.has_student_submissions = real_subs

# 15. add: a failed POST is reported, the others still go in, and the exit
# code is non-zero so the file is not blindly re-run.
def run_add(questions, fail_on):
    stored = {}
    def post(quiz, q):
        if len(stored) + 1 == fail_on and fail_on not in stored.values():
            stored[f"fail{fail_on}"] = fail_on
            raise RuntimeError("HTTP 500")
        qid = 1000 + len(stored)
        stored[qid] = copy.deepcopy(q)
        return {"id": qid}
    saved = qt.post_question, qt.get_question_raw
    qt.post_question = post
    qt.get_question_raw = lambda quiz, qid: dict(stored[qid], id=qid)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(questions, f)
    quiz = types.SimpleNamespace(id=2, title="Quiz")
    course = types.SimpleNamespace(id=1, get_quiz=lambda _id: quiz)
    args = types.SimpleNamespace(file=f.name, quiz_id=2, dry_run=False, force=False, republish=False)
    out, err, code = io.StringIO(), io.StringIO(), 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            qt.cmd_add(course, args)
        except SystemExit as e:
            code = e.code
    qt.post_question, qt.get_question_raw = saved
    return code, out.getvalue() + err.getvalue(), [k for k in stored if isinstance(k, int)]

ess = lambda n: {"question_name": n, "question_type": "essay_question",
                 "question_text": f"<p>{n}</p>", "points_possible": 1}
code, out, made = run_add([ess("A"), ess("B"), ess("C")], fail_on=2)
check("add continues past a failed question", len(made) == 2, (made, out))
check("add exits non-zero and names the missing one", code and "#2" in out and "NOT added" in out, out)

# 16. Formula rounding matches Canvas (halves away from zero), and bad ranges
# or complex results fail clearly instead of crashing.
check("answers round half away from zero", qt.round_half_away(0.125, 2) == 0.13
      and qt.round_half_away(2.5) == 3 and qt.round_half_away(-2.5) == -3)
check("formula round() rounds half away", qt.evaluate_formulas(["round(x)"], {"x": 2.5}) == 3)
bad = {"question_type": "calculated_question", "question_text": "<p>[x]</p>", "formulas": ["x"],
       "variables": [{"name": "x", "min": 0.1, "max": 0.9, "scale": 0}], "answer_count": 3}
with contextlib.redirect_stderr(io.StringIO()) as err:
    try:
        qt.prepare(bad); code = 0
    except SystemExit as e:
        code = e.code
check("no whole number in range is a clear error", code and "no whole number" in str(code) + err.getvalue(), (code, err.getvalue()))
cplx = {"question_type": "calculated_question", "question_text": "<p>[x]</p>", "formulas": ["(0 - x)^0.5"],
        "variables": [{"name": "x", "min": 1, "max": 9, "scale": 0}], "answer_count": 3}
with contextlib.redirect_stderr(io.StringIO()) as err:
    qt.prepare(cplx)
check("complex results are skipped, not a crash", cplx["answers"] == [] and "only 0" in err.getvalue(), cplx.get("answers"))

# 17. Blank ids Canvas's editor allows ([1], [blank-2]) validate.
fimb = {"question_type": "fill_in_multiple_blanks_question", "question_text": "<p>[1] and [blank-2]</p>",
        "answers": [{"text": "a", "weight": 100, "blank_id": "1"}, {"text": "b", "weight": 100, "blank_id": "blank-2"}]}
qt.prepare(fimb)
check("numeric and dashed blank ids accepted", qt.validate(fimb, 0) == [], qt.validate(fimb, 0))
fimb["answers"][0]["blank_id"] = "3"
check("an answer for a blank not in the text is still caught", any("[3]" in p for p in qt.validate(fimb, 0)))

# 18. The submission check uses only Canvas's yes/no flags, and blocks writes.
class FlagRequester:
    def __init__(self, quiz_json, assignment_json):
        self.paths, self.q, self.a = [], quiz_json, assignment_json
    def request(self, method, path, **kw):
        self.paths.append((method, path))
        body = self.a if "/assignments/" in path else self.q
        return types.SimpleNamespace(json=lambda: dict(body))
def flag_quiz(q, a):
    return types.SimpleNamespace(id=2, course_id=1, title="Quiz", _requester=FlagRequester(q, a))
fq = flag_quiz({"unpublishable": False}, {})
check("unpublishable=false means taken", real_subs(fq))
fq = flag_quiz({"unpublishable": True, "assignment_id": 9}, {"has_submitted_submissions": True})
check("has_submitted_submissions means taken", real_subs(fq))
check("only quiz and assignment endpoints read", fq._requester.paths ==
      [("GET", "courses/1/quizzes/2"), ("GET", "courses/1/assignments/9")], fq._requester.paths)
fq = flag_quiz({"unpublishable": True, "assignment_id": 9}, {"has_submitted_submissions": False})
check("untaken quiz is not taken", not real_subs(fq))
saved_cw, saved_subs = qt.check_writable, qt.has_student_submissions
qt.check_writable = real_check_writable
qt.has_student_submissions = lambda quiz: True
with contextlib.redirect_stderr(io.StringIO()) as err:
    try:
        qt.check_writable(types.SimpleNamespace(title="Quiz"), False); code = 0
    except SystemExit as e:
        code = e.code
check("taken quiz refused without --force", code and "student submissions" in str(code) + err.getvalue())
with contextlib.redirect_stdout(io.StringIO()):
    forced = qt.check_writable(types.SimpleNamespace(title="Quiz"), True)
check("--force lets it through", forced is True)
qt.check_writable, qt.has_student_submissions = saved_cw, saved_subs

# 19. differences() compares answers, not just text.
mc = {"question_type": "multiple_choice_question", "question_text": "<p>q</p>", "points_possible": 1,
      "answers": [{"answer_text": "a", "answer_weight": 100}, {"answer_text": "b", "answer_weight": 0}]}
got = dict(mc, answers=[{"text": "a", "weight": 0}, {"text": "b", "weight": 100}])
check("a moved answer key is a difference", qt.differences(mc, got), qt.differences(mc, got))

# 20. cmd_edit: re-read before writing, formula regeneration, group id.
def edit_harness(live, changes, change_between=None):
    state = {"live": copy.deepcopy(live), "reads": 0, "sent": []}
    def get_raw(quiz, qid):
        state["reads"] += 1
        if change_between and state["reads"] == 2:
            state["live"].update(change_between)
        return copy.deepcopy(state["live"])
    def put(quiz, qid, q):
        state["sent"].append(copy.deepcopy(q))
        state["live"].update(copy.deepcopy(q))
    saved = qt.get_question_raw, qt.put_question, qt.backup_question
    qt.get_question_raw, qt.put_question = get_raw, put
    qt.backup_question = lambda live: state.setdefault("backups", []).append(live["id"]) or "backup.json"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(changes, f)
    quiz = types.SimpleNamespace(course_id=1, id=2, title="Quiz")
    course = types.SimpleNamespace(id=1, get_quiz=lambda _id: quiz)
    args = types.SimpleNamespace(file=f.name, quiz_id=2, question_id=live["id"], expect=None,
                                 dry_run=False, force=False, republish=False)
    out, code = io.StringIO(), 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            qt.cmd_edit(course, args)
        except SystemExit as e:
            code = e.code
    qt.get_question_raw, qt.put_question, qt.backup_question = saved
    return code, out.getvalue(), state

code, out, st = edit_harness(TF_LIVE, {"points_possible": 3}, change_between={"question_text": "<p>someone else</p>"})
check("a change between prepare and write aborts", code and not st["sent"] and "changed while" in str(code) + out, out)

CALC_LIVE = {"id": 20, "quiz_id": 2, "question_type": "calculated_question", "question_name": "Calc",
             "question_text": "<p>[n] items</p>", "points_possible": 1.0, "quiz_group_id": 7,
             "correct_comments": "", "incorrect_comments": "", "neutral_comments": "",
             "formulas": [{"formula": "n*n"}], "variables": [{"name": "n", "min": 2, "max": 9, "scale": 0}],
             "answer_tolerance": 0, "formula_decimal_places": 0,
             "answers": [{"variables": [{"name": "n", "value": "3"}], "answer": 9},
                         {"variables": [{"name": "n", "value": "4"}], "answer": 16}]}
code, out, st = edit_harness(CALC_LIVE, {"points_possible": 2})
sent = st["sent"][0] if st["sent"] else {}
check("points-only edit keeps the answer sets", [a["answer_text"] for a in sent.get("answers", [])] == [9, 16], sent.get("answers"))
check("an unchanged group id is not re-sent", "quiz_group_id" not in sent, sorted(sent))
code, out, st = edit_harness(CALC_LIVE, {"variables": [{"name": "n", "min": 50, "max": 60, "scale": 0}]})
sent = st["sent"][0] if st["sent"] else {}
vals = [int(a["variables"][0]["value"]) for a in sent.get("answers", [])]
check("a new variable range regenerates the sets", len(vals) == 2 and all(50 <= v <= 60 for v in vals)
      and all(a["answer_text"] == int(a["variables"][0]["value"]) ** 2 for a in sent["answers"]), sent.get("answers"))

# 21. Paths the mutation run showed were untested.
def quiet(fn, *a, **kw):
    out, code = io.StringIO(), 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            fn(*a, **kw)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()

# add: a question Canvas stores differently is a failure.
saved = qt.post_question, qt.get_question_raw
qt.post_question = lambda quiz, q: {"id": 1}
qt.get_question_raw = lambda quiz, qid: {"id": 1, "question_type": "essay_question",
                                         "question_text": "<p>NOT what was sent</p>", "points_possible": 1.0}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump([ess("A")], f)
quiz = types.SimpleNamespace(id=2, title="Quiz")
code, out = quiet(qt.cmd_add, types.SimpleNamespace(id=1, get_quiz=lambda _id: quiz),
                  types.SimpleNamespace(file=f.name, quiz_id=2, dry_run=False, force=False, republish=False))
qt.post_question, qt.get_question_raw = saved
check("add notices Canvas stored something else", code and "do not match" in out, out)

# a group counts as its pick_count.
gq = types.SimpleNamespace(id=2, course_id=1, _requester=types.SimpleNamespace(
    request=lambda m, p, **kw: types.SimpleNamespace(json=lambda: {"pick_count": 2})))
members = [types.SimpleNamespace(question_type="essay_question", quiz_group_id=5) for _ in range(3)]
loose = [types.SimpleNamespace(question_type="essay_question", quiz_group_id=None),
         types.SimpleNamespace(question_type="text_only_question", quiz_group_id=None)]
check("expected count: loose + pick_count", qt.expected_question_count(gq, members + loose) == 3)

# edit and delete back up first.
backups = []
saved_b = qt.backup_question
qt.backup_question = lambda live: backups.append(live["id"]) or "b.json"
code, out, st = edit_harness(TF_LIVE, {"points_possible": 3})
check("edit backs up before writing", st.get("backups") == [10] and st["sent"], st.get("backups"))
deleted = []
dq = types.SimpleNamespace(id=2, course_id=1, title="Quiz", get_question=lambda qid: types.SimpleNamespace(
    id=qid, question_type="essay_question", points_possible=1.0, question_text="<p>x</p>",
    question_name="Q", answers=[], delete=lambda: deleted.append(qid)))
saved_g = qt.get_question_raw
qt.get_question_raw = lambda quiz, qid: {"id": qid}
code, out = quiet(qt.cmd_delete, types.SimpleNamespace(id=1, get_quiz=lambda _id: dq),
                  types.SimpleNamespace(quiz_id=2, question_id=77, dry_run=False, yes=True, force=False, republish=False))
qt.get_question_raw, qt.backup_question = saved_g, saved_b
check("delete backs up, then deletes", backups[-1] == 77 and deleted == [77] and "restore it with" in out, (backups, out))

# matching: a missing distractor field is sent as "", never left out.
MATCH_LIVE = {"id": 30, "quiz_id": 2, "question_type": "matching_question", "question_name": "M",
              "question_text": "<p>m</p>", "points_possible": 1.0, "correct_comments": "",
              "incorrect_comments": "", "neutral_comments": "",
              "answers": [{"left": "a", "right": "1", "id": 1}, {"left": "b", "right": "2", "id": 2}]}
code, out, st = edit_harness(MATCH_LIVE, {"points_possible": 2})
sent = st["sent"][0] if st["sent"] else {}
check("matching distractors sent as empty, not missing", sent.get("matching_answer_incorrect_matches") == "", sorted(sent))

# settings: a change Canvas ignores is a failure, with the reason.
class SettingsQuiz:
    def __init__(self):
        self.id, self.course_id, self.state = 2, 1, {"title": "Q", "allowed_attempts": 1,
                                                     "scoring_policy": "keep_highest", "question_count": 1}
        self._requester = self
    def request(self, method, path, **kw):
        return types.SimpleNamespace(json=lambda: dict(self.state))
    def edit(self, quiz):
        attempts = quiz.get("allowed_attempts", self.state["allowed_attempts"])
        if quiz.get("scoring_policy") and attempts <= 1:
            quiz = {k: v for k, v in quiz.items() if k != "scoring_policy"}   # Canvas drops it
        self.state.update(quiz)
sq = SettingsQuiz()
sargs = types.SimpleNamespace(quiz_id=2, dry_run=False, force=False, **{k: None for k in qt.QUIZ_SETTINGS})
sargs.scoring_policy = "keep_latest"
saved_s = qt.has_student_submissions
qt.has_student_submissions = lambda quiz: False
code, out = quiet(qt.cmd_settings, types.SimpleNamespace(id=1, get_quiz=lambda _id: sq), sargs)
check("settings flags a change Canvas ignored", code and "✗ scoring_policy" in out and "allowed_attempts" in out, out)
sargs.allowed_attempts = 2
code, out = quiet(qt.cmd_settings, types.SimpleNamespace(id=1, get_quiz=lambda _id: SettingsQuiz()), sargs)
check("settings passes when both are set", not code and "✗" not in out, out)
qt.has_student_submissions = saved_s

# find numbers questions as Canvas does (a group takes pick_count numbers).
class FindQuiz:
    id, course_id, title, published = 2, 1, "Quiz", False
    def __init__(self):
        self._requester = self
    def request(self, method, path, **kw):
        body = {"pick_count": 1, "position": 1, "name": "pool"} if "/groups/" in path else {"question_count": 2}
        return types.SimpleNamespace(json=lambda: body)
    def get_questions(self):
        mk = lambda i, g, t: types.SimpleNamespace(id=i, quiz_group_id=g, question_type="essay_question",
                                                   question_text=t, question_name="", answers=[], points_possible=1.0)
        return [mk(1, 5, "<p>pool one</p>"), mk(2, 5, "<p>pool two</p>"), mk(3, None, "<p>target here</p>")]
fargs = types.SimpleNamespace(pattern="target", regex=False, case_sensitive=False, answers=False, type=None, quiz=None)
code, out = quiet(qt.cmd_find, types.SimpleNamespace(name="C", get_quizzes=lambda: [FindQuiz()]), fargs)
check("find labels a question after a group with Canvas's number", "Q2  id=3" in out, out)

print("\nfailures:", failures)
sys.exit(failures)
