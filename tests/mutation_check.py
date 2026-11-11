"""Mutation check for the offline tests: break the code on purpose, one way
at a time, and confirm a test fails. Run: python3 tests/mutation_check.py

Each entry is (name, script, test file, original text, broken text). The
repo is copied to a temp dir for each run; nothing here touches Canvas.
Three entries are known to survive because other code gives the same result
(EQUIVALENT below); any other survivor means a test is missing.
"""
import pathlib, shutil, subprocess, sys, tempfile

CC, QT = "canvas_course.py", "quiz_tool.py"
TC, TQ = "test_canvas_course.py", "test_quiz_tool.py"
M = [
 ("plan-id check removed", CC, TC, "        elif known.apply != pid:", "        elif False:"),
 ("mismatch counted as ok", CC, TC, '                if not same(got, c["after"]):', "                if False:"),
 ("error does not stop run", CC, TC, '"attempted. Earlier changes were applied (see journal).")\n                sys.exit(1)', '"attempted. Earlier changes were applied (see journal).")\n                continue'),
 ("unchanged not skipped", CC, TC, 'if field not in ("create", "delete") and same(before, after):', "if False:"),
 ("no-tty apply not refused", CC, TC, "if not sys.stdin.isatty():", "if False:"),
 ("journal omits before", CC, TC, 'entry = {k: c[k] for k in ("target", "field", "before", "after")}', 'entry = {k: c[k] for k in ("target", "field", "after")}'),
 ("same ignores missing key", CC, TC, "return a.keys() == b.keys() and all(", "return all("),
 ("bare date at noon", CC, TC, "dt = dt.replace(hour=23, minute=59)", "dt = dt.replace(hour=12, minute=0)"),
 ("connect skips everyone_dates", CC, TC, "    everyone_dates(course)\n", ""),
 ("script not in plan id", CC, TC, "digest.update(script_source())", "pass"),
 ("journal overwritten", CC, TC, "while journal.exists():", "while False:"),
 ("sets unstable in id", CC, TC, "sort_keys=True, default=stable)", "sort_keys=True, default=str)"),
 ("created id not journaled", CC, TC, 'entry["created"] = describe_created(result)', "pass"),
 ("duplicate guard off", CC, TC, "        repeats = self.already_created()", "        repeats = {}"),
 ("error journals count as made", CC, TC, 'startswith(("ok", "mismatch"))', 'startswith(("ok", "mismatch", "error"))'),
 ("same() html exact", CC, TC, '"<" in a + b:\n        return same_html(a, b)', '"<" in a + b:\n        return a == b'),
 ("unpublishable flag ignored", QT, TQ, '    if raw.get("unpublishable") is False:\n        return True', "    if False:\n        return True"),
 ("republish never publishes", QT, TQ, '                quiz.edit(quiz={"published": True})', "                pass"),
 ("republishes by default", QT, TQ, "        if not republish:", "        if False:"),
 ("submitted quiz toggled", QT, TQ, "            print(\"  !! This quiz is published and students have submitted, so \"", "            pass\n        if False:\n            print(\"\""),
 ("add skips read-back", QT, TQ, '            diffs = differences(q, get_question_raw(quiz, created["id"]))', "            diffs = []"),
 ("add partial failure exits 0", QT, TQ, "    if failed:\n        # Re-running", "    if False:\n        # Re-running"),
 ("edit no re-read check", QT, TQ, "    if fingerprint(current) != base_fp:", "    if False:"),
 ("formula sets not regenerated", QT, TQ, '        merged.pop("answers", None)', "        pass"),
 ("group id re-sent", QT, TQ, '        merged.pop("quiz_group_id", None)', "        pass"),
 ("no-op edit writes", QT, TQ, '        print("  already as requested; nothing was sent")\n        return', '        print("  already as requested; nothing was sent")'),
 ("python rounding", QT, TQ, "decimal.ROUND_HALF_UP", "decimal.ROUND_HALF_EVEN"),
 ("blank ids strict", QT, TQ, '                elif f"[{extra}]" not in (text or ""):', '                elif extra not in blanks_in(text):'),
 ("unpublished not resaved", QT, TQ, '            quiz.edit(quiz={"title": title})', "            pass"),
 ("group counted as members", QT, TQ, '    return count + sum(quiz_group(quiz, gid).get("pick_count") or 0', '    return count + 0 * sum(quiz_group(quiz, gid).get("pick_count") or 0'),
 ("edit keeps no backup", QT, TQ, "    backup = backup_question(current)", '    backup = "none"'),
 ("delete keeps no backup", QT, TQ, "    backup = backup_question(get_question_raw(quiz, question.id))", '    backup = "none"'),
 ("settings ignores ignored", QT, TQ, "        if not stuck:\n            ignored.append(field)", "        if False:\n            ignored.append(field)"),
 ("find numbers ignore groups", QT, TQ, "        for label, q, _ in display_order(quiz, questions):", "        for label, q, _ in [(f'Q{n}', q, None) for n, q in enumerate(questions, 1)]:"),
 ("matching distractors null", QT, TQ, '        merged["matching_answer_incorrect_matches"] = ""', "        pass"),
 ("dump date fields kept", QT, TQ, '    for key in [k for k in question if k.endswith("_date")]:\n        question.pop(key)', "    pass"),
]

EQUIVALENT = {"formula sets not regenerated", "group id re-sent", "matching distractors null"}


def main():
    repo = pathlib.Path(__file__).resolve().parents[1]
    survived = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, src, test, old, new in M:
            work = pathlib.Path(tmp) / "w"
            shutil.rmtree(work, ignore_errors=True)
            shutil.copytree(repo / "plugins", work / "plugins")
            shutil.copytree(repo / "tests", work / "tests")
            f = work / "plugins/jmu-canvas/scripts" / src
            text = f.read_text()
            if text.count(old) != 1:
                print(f"??       {name}: original text found {text.count(old)} times; update this entry")
                survived.append(name)
                continue
            f.write_text(text.replace(old, new))
            r = subprocess.run([sys.executable, str(work / "tests" / test)],
                               capture_output=True, text=True, timeout=300)
            if r.returncode:
                print(f"caught   {name}")
            else:
                print(f"MISSED   {name}" + ("  (equivalent)" if name in EQUIVALENT else ""))
                survived.append(name)
    unexpected = [n for n in survived if n not in EQUIVALENT]
    print(f"\n{len(M) - len(survived)}/{len(M)} caught; {len(unexpected)} unexpected miss(es)")
    sys.exit(1 if unexpected else 0)


if __name__ == "__main__":
    main()
