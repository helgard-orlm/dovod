#!/bin/sh
# Dovod self-test: good programs must pass (also under sanitizers), bad ones must fail with the expected codes.
cd "$(dirname "$0")/.." || exit 1
fail=0
for f in examples/hello.dv examples/shapes.dv examples/wordfreq.dv examples/bench.dv examples/player.dv tests/stress.dv; do
  bin/dovod check "$f" >/dev/null 2>&1 || { echo "FAIL (should pass): $f"; fail=1; }
done
out=$(DOVOD_CFLAGS="-fsanitize=address,undefined -g" bin/dovod run tests/stress.dv 2>&1)
echo "$out" | grep -q "ERROR\|runtime error\|LeakSanitizer" && { echo "FAIL: sanitizer report in stress.dv"; fail=1; }
want_slips="E-MATCH E-NAME E-TYPE E-VAR E-EVIDENCE E-SHADOW E-UNUSED E-EFFECT E-HOLE"
out=$(bin/dovod check tests/bad/slips.dv 2>&1) && { echo "FAIL (should fail): slips.dv"; fail=1; }
for c in $want_slips; do echo "$out" | grep -q "$c" || { echo "FAIL: slips.dv did not report $c"; fail=1; }; done
out=$(bin/dovod check tests/bad/broken_examples.dv 2>&1) && { echo "FAIL (should fail): broken_examples.dv"; fail=1; }
echo "$out" | grep -q "got 0, expected 1" || { echo "FAIL: no got/expected"; fail=1; }
echo "$out" | grep -q "need failed" || { echo "FAIL: no need trap"; fail=1; }
bin/dovod trust tests/bad/evidence.dv | grep -q "affects: norm, main" || { echo "FAIL: trust ledger"; fail=1; }
out=$(bin/dovod check tests/vacuous/a_vacuous.dv 2>&1)
[ "$(echo "$out" | grep -c E-VACUOUS)" = 4 ] || { echo "FAIL: acceptance A (vacuous examples) not all flagged"; fail=1; }
out=$(bin/dovod check tests/vacuous/c_holes_glm.dv 2>&1)
[ "$(echo "$out" | grep -cE "E-VACUOUS|E-EVIDENCE")" = 5 ] || { echo "FAIL: glm bypasses (L0/L1/L2) not all rejected"; fail=1; }
[ "$(bin/dovod check tests/vacuous/d_sentinel.dv 2>&1 | grep -c E-VACUOUS)" = 2 ] || { echo "FAIL: sentinel collision"; fail=1; }
bin/dovod check tests/vacuous/b_meaningful.dv >/dev/null 2>&1 || { echo "FAIL: acceptance B (meaningful examples) flagged"; fail=1; }
DOVOD_CFLAGS="-fsanitize=address,undefined -g" bin/dovod check tests/regress_glm_review.dv >/dev/null 2>&1 || { echo "FAIL: regress_glm_review.dv"; fail=1; }
bin/dovod trust tests/witness_shell/t.dv --witness tests/witness_shell/w.dv | grep -q "double    self-checked" || { echo "FAIL: shell witness counted"; fail=1; }
for f in exam/runs/*/*/solution.dv; do bin/dovod check "$f" >/dev/null 2>&1 || { echo "FAIL (exam solution regressed): $f"; fail=1; }; done
if command -v x86_64-w64-mingw32-gcc >/dev/null; then  # cross builds: a Windows .exe, GUI subsystem for `video`
  t=$(mktemp -d)
  bin/dovod build examples/player.dv --target win64 -o "$t/p" >/dev/null 2>&1 && file "$t/p.exe" | grep -q "(GUI), x86-64" || { echo "FAIL: win64 build of player.dv"; fail=1; }
  bin/dovod build examples/wordfreq.dv --target win32 -o "$t/w" >/dev/null 2>&1 && file "$t/w.exe" | grep -q "(console), Intel i386" || { echo "FAIL: win32 build of wordfreq.dv"; fail=1; }
  rm -f "$t"/p.exe "$t"/w.exe; rmdir "$t"
fi
[ $fail = 0 ] && echo "all tests passed"
exit $fail
