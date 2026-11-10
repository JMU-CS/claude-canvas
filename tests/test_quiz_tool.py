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
                                 expect=expect, dry_run=dry, force=False)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            qt.cmd_edit(course, args)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


qt.check_writable = lambda quiz, force: 0
qt.resave = lambda quiz: None
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
code, out, err = run_edit(fake, {"answers": [{"text": t, "weight": 0} for t in "abcde"]})
check("5 answers on T/F refused", code != 0 and not fake.puts, err)

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

print("\nfailures:", failures)
sys.exit(failures)
