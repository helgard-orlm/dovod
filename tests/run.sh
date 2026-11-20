#!/bin/sh
# Dovod self-test: good programs must pass (also under sanitizers), bad ones must fail with the expected codes.
cd "$(dirname "$0")/.." || exit 1
fail=0
for f in examples/hello.dv examples/shapes.dv examples/wordfreq.dv examples/bench.dv examples/player.dv tests/stress.dv tests/v02.dv; do
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
out=$(DOVOD_CFLAGS="-fsanitize=address,undefined -g" bin/dovod run tests/v02.dv 2>&1)
echo "$out" | grep -q "ERROR\|runtime error\|LeakSanitizer" && { echo "FAIL: sanitizer report in v02.dv"; fail=1; }
echo "$out" | grep -q 'inner: Q(a: 8, t: "qz")) 2zeroyestrue' || { echo "FAIL: v02.dv output"; fail=1; }
out=$(bin/dovod check tests/bad/v02_slips.dv 2>&1) && { echo "FAIL (should fail): v02_slips.dv"; fail=1; }
[ "$(echo "$out" | grep -cE "^tests/bad/v02_slips.dv:[0-9]+:[0-9]+: E-(NAME|TYPE|MATCH|ARGS)")" = 8 ] || { echo "FAIL: v02_slips.dv diagnostics"; fail=1; }
# strength: body mutants. weak.dv has one example that pins the result but not the boundary
out=$(bin/dovod weak tests/weak.dv 2>&1)
echo "$out" | grep -q "W-WEAK: in \`grade\` no example notices if \`>=\` becomes \`>\`" || { echo "FAIL: weak.dv boundary mutant not reported"; fail=1; }
echo "$out" | grep -q "W-WEAK: in \`tidy\` no example notices if this assignment is removed" || { echo "FAIL: weak.dv removed assignment not reported"; fail=1; }
echo "$out" | grep -q "in \`spin\`" && { echo "FAIL: weak.dv endless-loop mutant should count as noticed"; fail=1; }
bin/dovod trust examples/player.dv | grep -q "module .video.: 13 native function(s), [0-9]* lines of C" || { echo "FAIL: ledger does not name the video runtime"; fail=1; }
# modules: native code behind `use NAME`, stubs on other targets, names instead of namespaces
out=$(cd tests/mod && DOVOD_CFLAGS="-fsanitize=address,undefined -g" ../../bin/dovod run prog.dv 2>&1)
echo "$out" | grep -q "ERROR\|runtime error\|LeakSanitizer" && { echo "FAIL: sanitizer report in tests/mod/prog.dv"; fail=1; }
echo "$out" | grep -q '42 ABC some("x") none one,two 50 5.0 DemoReading(id: 7, label: "TEMP")' || { echo "FAIL: module demo output"; fail=1; }
(cd tests/mod && ../../bin/dovod run prog.dv a 2>&1) | grep -q "need failed: pct >= 0 and pct <= 100" || { echo "FAIL: extern need not checked"; fail=1; }
(cd tests/mod && ../../bin/dovod run prog.dv a b 2>&1) | grep -q "has native code only for: win64; this build is native" || { echo "FAIL: no stub trap for a module without native code here"; fail=1; }
[ "$(cd tests/mod && ../../bin/dovod check bad.dv 2>&1 | grep -c "looks like it belongs to module .demo.")" = 2 ] || { echo "FAIL: reserved module prefix not enforced"; fail=1; }
(cd tests/mod && ../../bin/dovod check bad2.dv 2>&1) | grep -q "E-EFFECT: .quiet. does .demo." || { echo "FAIL: module effect leaked into a pure function"; fail=1; }
[ "$(cd tests/mod && ../../bin/dovod check bad3.dv 2>&1 | grep -c "E-MODULE")" = 4 ] || { echo "FAIL: badmod rules"; fail=1; }
(cd tests/mod && ../../bin/dovod check bad4.dv 2>&1) | grep -q "allowed only in a module file" || { echo "FAIL: extern outside a module accepted"; fail=1; }
(cd tests/mod && ../../bin/dovod trust prog.dv) | grep -q "module demo: 6 native function(s) (11 lines of C" || { echo "FAIL: ledger module line"; fail=1; }
bin/dovod check dovod/modules/video/video.dv >/dev/null 2>&1 || { echo "FAIL: module video does not check on its own"; fail=1; }
bin/dovod spec video | grep -q "need pct >= 25 and pct <= 400" || { echo "FAIL: spec video"; fail=1; }
# module ui: checks on its own, its example program checks, and the served page works end to end
bin/dovod check dovod/modules/ui/ui.dv >/dev/null 2>&1 || { echo "FAIL: module ui does not check on its own"; fail=1; }
bin/dovod check examples/prices.dv >/dev/null 2>&1 || { echo "FAIL: prices.dv (ui example)"; fail=1; }
bin/dovod check examples/typing.dv >/dev/null 2>&1 || { echo "FAIL: typing.dv (ui example)"; fail=1; }
bin/dovod spec ui | grep -q "show a window or page" || { echo "FAIL: spec ui"; fail=1; }
t2=$(mktemp -d)
bin/dovod build tests/ui_serve.dv -o "$t2/serve" >/dev/null 2>&1 || { echo "FAIL: build tests/ui_serve.dv"; fail=1; }
if [ -x "$t2/serve" ]; then
  DOVOD_UI_NOWINDOW=1 "$t2/serve" & upid=$!
  sleep 1
  curl -s http://127.0.0.1:8471/ | grep -q "<!doctype html>" || { echo "FAIL: ui page not served"; fail=1; }
  curl -s -X POST --data-binary 'click:quit
0' http://127.0.0.1:8471/event >/dev/null || { echo "FAIL: /event not accepted"; fail=1; }
  wait $upid || { echo "FAIL: ui_serve did not exit after the click"; fail=1; }
fi
rm -rf "$t2"
# microcontrollers: the arduino target writes a sketch; its C runs here on a pretend board (tests/mcu/sim.c)
t=build/test_mcu; mkdir -p "$t"
bin/dovod build examples/lamp.dv --target arduino -o "$t/lamp" >/dev/null 2>&1 && [ -f "$t/lamp/lamp.ino" ] || { echo "FAIL: arduino sketch not written"; fail=1; }
cc -std=gnu11 -DARDUINO -fsanitize=address,undefined -g -w -o "$t/lampsim" "$t/lamp/dovod_prog.c" tests/mcu/sim.c -lm 2>/dev/null || { echo "FAIL: lamp sketch does not compile for the pretend board"; fail=1; }
out=$("$t/lampsim" 2>&1)
echo "$out" | grep -q "ERROR\|runtime error" && { echo "FAIL: sanitizer report in the lamp sketch"; fail=1; }
echo "$out" | grep -q "^330 say mode: On" && echo "$out" | grep -q "^1630 say mode: Blink" || { echo "FAIL: lamp on the pretend board (short press -> On at 330, long -> Blink at 1630)"; fail=1; }
bin/dovod build tests/mcu/all.dv --target arduino -o "$t/all" >/dev/null 2>&1
cc -std=gnu11 -DARDUINO -fsanitize=address,undefined -g -w -o "$t/allsim" "$t/all/dovod_prog.c" tests/mcu/sim.c -lm 2>/dev/null || { echo "FAIL: mcu/all sketch does not compile"; fail=1; }
out=$("$t/allsim" 2>&1)
echo "$out" | grep -q "ERROR\|runtime error" && { echo "FAIL: sanitizer report in mcu/all"; fail=1; }
echo "$out" | grep -q "pwm pin5=99" && echo "$out" | grep -q "say heard ping" && echo "$out" | grep -q "say heard nothing" || { echo "FAIL: mcu/all output"; fail=1; }
echo "$out" | grep -q "TRAP need failed: duty >= 0 and duty <= 255" || { echo "FAIL: mcu need not checked on the board"; fail=1; }
bin/dovod run tests/mcu/all.dv 2>&1 | grep -q "has native code only for: arduino; this build is native" || { echo "FAIL: mcu call on the PC should trap with a clear message"; fail=1; }
if command -v arduino-cli >/dev/null && arduino-cli core list 2>/dev/null | grep -q "arduino:avr"; then  # a real 8-bit board
  bin/dovod build examples/lamp.dv --target arduino -o "$t/lamp" --fqbn arduino:avr:leonardo 2>&1 | grep -q "28672" || { echo "FAIL: lamp does not build for arduino:avr:leonardo"; fail=1; }
fi
bin/dovod trust tests/bad/evidence.dv | grep -q "affects: norm, main" || { echo "FAIL: trust ledger"; fail=1; }
out=$(bin/dovod check tests/vacuous/a_vacuous.dv 2>&1)
[ "$(echo "$out" | grep -c E-VACUOUS)" = 4 ] || { echo "FAIL: acceptance A (vacuous examples) not all flagged"; fail=1; }
out=$(bin/dovod check tests/vacuous/c_holes_glm.dv 2>&1)
[ "$(echo "$out" | grep -cE "E-VACUOUS|E-EVIDENCE")" = 5 ] || { echo "FAIL: glm bypasses (L0/L1/L2) not all rejected"; fail=1; }
[ "$(bin/dovod check tests/vacuous/d_sentinel.dv 2>&1 | grep -c E-VACUOUS)" = 2 ] || { echo "FAIL: sentinel collision"; fail=1; }
bin/dovod check tests/vacuous/b_meaningful.dv >/dev/null 2>&1 || { echo "FAIL: acceptance B (meaningful examples) flagged"; fail=1; }
DOVOD_CFLAGS="-fsanitize=address,undefined -g" bin/dovod check tests/regress_glm_review.dv >/dev/null 2>&1 || { echo "FAIL: regress_glm_review.dv"; fail=1; }
bin/dovod trust tests/witness_shell/t.dv --witness tests/witness_shell/w.dv | grep -q "double    self-checked" || { echo "FAIL: shell witness counted"; fail=1; }
# exam solutions that were committed as passing (a fresh, uncommitted run may hold a model's failures)
for f in $(git ls-files 'exam/runs/*/*/solution.dv'); do bin/dovod check "$f" >/dev/null 2>&1 || { echo "FAIL (exam solution regressed): $f"; fail=1; }; done
# 0.4: Map, sort, multi-variant arms
out=$(DOVOD_CFLAGS="-fsanitize=address,undefined -g" bin/dovod run tests/maps.dv 2>&1)
echo "$out" | grep -q "ERROR\|runtime error\|LeakSanitizer" && { echo "FAIL: sanitizer report in maps.dv"; fail=1; }
echo "$out" | grep -q '^100000 false some(100001) \[3, 10, 17\]$' || { echo "FAIL: maps.dv big map"; fail=1; }
echo "$out" | grep -q '^{"cat": 1, "dog": 1, "end": 1, "the": 3} the \[1, 1, 1, 3\]$' || { echo "FAIL: maps.dv word count"; fail=1; }
echo "$out" | grep -q '^{2: Stock(name: "b", qty: 10)}$' || { echo "FAIL: maps.dv records in a map"; fail=1; }
# 0.4: habits of other languages are named, with what Dovod writes instead
th=$(mktemp)
habit() {   # $1 = program text, $2 = text that must appear in the diagnostics
  printf '%s\n' "$1" > "$th"
  bin/dovod check "$th" 2>&1 | grep -qF -- "$2" || { echo "FAIL: habit not explained: $2"; fail=1; }
}
habit 'def f(x):' 'a function is `fn name(x: Int)'
habit 'fn f(xs: List[Int]) -> Bool
  ex f([1]) == true
{
  return all(x > 0 for x in xs)
}' 'no comprehensions or generator expressions'
habit 'fn main() uses io {
  var i = 0
  i += 1
  print(show(i))
}' 'there is no `+=`'
habit 'fn main() uses io {
  if 1 > 0:
}' 'a block is `{ ... }` on the same line'
habit 'fn main() uses io {
  print(show(slice([1, 2], 0, 1)[0:1]))
}' 'no slice syntax'
habit 'fn main() uses io {
  print(show(true && false))
}' 'Dovod writes `and`'
habit 'fn main() uses io {
  let x: Opt[Int] = None
  print(show(x))
}' 'write `none`'
habit 'fn main() uses io {
  for i in range(3) {
    print(show(i))
  }
}' 'loop with `for i in 0..n'
habit 'fn main() uses io {
  print(join("a,b".split(","), " "))
}' 'write `split(t, sep)`'
habit 'fn main() uses io {
  let xs = [1]
  print(show(xs[-1]))
}' 'the last element is `xs[len(xs) - 1]`'
habit 'fn main() uses io {
  let a = 1 if true else 2
  print(show(a))
}' 'no conditional expression'
habit 'fn main() uses io {
  print(show(1 in [1]))
}' 'no `in` operator'
habit 'fn main() uses io {
  let m: Map[Text, Int] = {"a": 1}
  for k in m {
    print(k)
  }
}' 'for k in keys(m)'
rm -f "$th"
# 0.4 modules: each checks on its own; pure ones carry their examples
bin/dovod check examples/notes.dv >/dev/null 2>&1 || { echo "FAIL: notes.dv (web + json + date + Map)"; fail=1; }
for m in text json date files proc web; do
  bin/dovod check "dovod/modules/$m/$m.dv" >/dev/null 2>&1 || { echo "FAIL: module $m does not check on its own"; fail=1; }
done
tj=$(mktemp)
printf '{"a": [1, 2.5, -0.0, "ё\\u0436\\n", true, null], "b": {"c": {}}, "d": 1e300}' > "$tj"
out=$(bin/dovod run tests/json_round.dv "$tj" 2>&1)
[ "$out" = "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])), ensure_ascii=False, separators=(",", ":")).replace("1e+300", "1e+300").replace("2.5", "2.5").replace("-0.0", "-0").replace("[1,", "[1,"))' "$tj")" ] || { echo "FAIL: json round trip: $out"; fail=1; }
rm -f "$tj"
tf=$(mktemp -d)
out=$(DOVOD_CFLAGS="-fsanitize=address,undefined -g" bin/dovod run tests/files_demo.dv "$tf" 2>&1)
echo "$out" | grep -q "ERROR\|runtime error\|LeakSanitizer" && { echo "FAIL: sanitizer report in files_demo.dv"; fail=1; }
[ "$(printf '%s\n' "$out" | tr '\n' '|')" = '[true, true, true, true]|some(["one.txt", "y"]) none|some(11) none|["x/one.txt", "x/y/ёж.txt"]|[false, true, true, true]|[false, true, true] ["x/y/ёж.txt"]|true|' ] || { echo "FAIL: files_demo.dv output"; fail=1; }
rm -rf "$tf"
out=$(DOVOD_CFLAGS="-fsanitize=address,undefined -g" bin/dovod run tests/proc_demo.dv 2>&1)
echo "$out" | grep -q "ERROR\|runtime error\|LeakSanitizer" && { echo "FAIL: sanitizer report in proc_demo.dv"; fail=1; }
[ "$(printf '%s\n' "$out" | tr '\n' '|')" = 'ProcResult(code: 0, out: "привет a b;c\n", err: "", timed_out: false)|ProcResult(code: 3, out: "out\n", err: "err\n", timed_out: false)|0 5|127 true|true -1|y|200000|' ] || { echo "FAIL: proc_demo.dv output"; fail=1; }
tw=$(mktemp -d)
if bin/dovod build tests/web_demo.dv -o "$tw/wd" >/dev/null 2>&1; then
  "$tw/wd" >/dev/null 2>&1 & wpid=$!
  sleep 1
  curl -s localhost:8473/ | grep -q "<h1>Довод</h1><p>0</p>" || { echo "FAIL: web page"; fail=1; }
  curl -s "localhost:8473/hello?name=%3Cx%3E" | grep -qF "hello, &lt;x&gt;" || { echo "FAIL: web param + escape"; fail=1; }
  [ "$(curl -s -o /dev/null -w '%{http_code}' -d 't=Новое' localhost:8473/add)" = 303 ] || { echo "FAIL: web form post"; fail=1; }
  curl -s localhost:8473/item/5 | grep -qF '{"id":"5","title":"Новое"}' || { echo "FAIL: web route + json + state"; fail=1; }
  curl -s localhost:8473/quit >/dev/null
  wait $wpid || { echo "FAIL: web_demo did not stop on /quit"; fail=1; }
else
  echo "FAIL: build tests/web_demo.dv"; fail=1
fi
rm -rf "$tw"
if command -v x86_64-w64-mingw32-gcc >/dev/null; then  # cross builds: a Windows .exe, GUI subsystem for `video`
  t=$(mktemp -d)
  bin/dovod build examples/player.dv --target win64 -o "$t/p" >/dev/null 2>&1 && file "$t/p.exe" | grep -q "(GUI), x86-64" || { echo "FAIL: win64 build of player.dv"; fail=1; }
  bin/dovod build examples/wordfreq.dv --target win32 -o "$t/w" >/dev/null 2>&1 && file "$t/w.exe" | grep -q "(console), Intel i386" || { echo "FAIL: win32 build of wordfreq.dv"; fail=1; }
  bin/dovod build examples/prices.dv --target win64 -o "$t/pr" >/dev/null 2>&1 || { echo "FAIL: win64 build of prices.dv (ui on Windows)"; fail=1; }
  rm -f "$t"/p.exe "$t"/w.exe "$t"/pr.exe; rmdir "$t"
fi
[ $fail = 0 ] && echo "all tests passed"
exit $fail
