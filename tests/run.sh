#!/bin/sh
# Dovod self-test: good programs must pass (also under sanitizers), bad ones must fail with the expected codes.
cd "$(dirname "$0")/.." || exit 1
fail=0
for f in examples/hello.dv examples/shapes.dv examples/wordfreq.dv examples/bench.dv tests/stress.dv; do
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
[ $fail = 0 ] && echo "all tests passed"
exit $fail
