# Dovod

**A small compiled language designed for AI agents to write — and for humans to trust without reading the code.**

*Dovod* (Russian «довод») means *an argument, a piece of evidence*. In Dovod, code carries its evidence:

- a pure function **does not compile** without examples (`ex`) — or an explicit `assume "why"`;
- an example must be able to fail: each one is re-run against two mutants of its function's result, and an example that still passes (`ex f(2) == f(2)`) is rejected; all examples run on every build;
- the compiler reports every block no example ever reached;
- effects are in the signature (`uses io, fs_read`), exactly — an undeclared *or unused* effect is an error, and there is no other way to reach the outside world;
- `dovod trust` prints a ledger: what the program can and cannot touch, the status of every function, and what is taken on faith — together with everything that depends on it.

It compiles to C (no garbage collector, single-owner value semantics, no references, no null), so it runs at native speed with every safety check on.

```
fn clamp(x: Int, lo: Int, hi: Int) -> Int
  need lo <= hi                        // precondition, checked on every call
  give out >= lo and out <= hi         // postcondition on the result `out`
  ex clamp(5, 0, 3) == 3               // examples: run at every check / build
  ex clamp(-1, 0, 3) == 0
  ex clamp(2, 0, 3) == 2
{
  if x < lo { return lo } else if x > hi { return hi }
  return x
}

fn main() uses io {
  print("clamp(15, 0, 10) = " + show(clamp(15, 0, 10)))
}
```

## Why

An AI writes code faster than anyone can check it. So the language makes checking the default rather than an afterthought, and it is shaped around how a model actually fails:

| An AI author…                                   | …so Dovod                                                                 |
|-------------------------------------------------|---------------------------------------------------------------------------|
| writes fast; checking is the bottleneck         | every pure function must carry examples; the compiler runs them           |
| can write tests that test nothing               | every example is re-run against mutants and must catch them; unreached branches are reported |
| reads code in fragments                         | no globals, no references, no hidden effects: a signature tells all       |
| makes small slips (wrong name, missed case)     | no shadowing, no unused names, exhaustive `match`, no implicit conversion |
| hands code to a human who will not read it      | `dovod trust` prints what the program can touch and what is taken on faith|
| has never seen this language                    | one way to do each thing; the whole spec is one 9 KB file                 |

Every diagnostic has a `file:line:col`, a stable code and usually a concrete `fix:`; `--json` gives the same to a machine.

```
slips.dv:18:5: E-NAME: unknown name `totl`
    fix: did you mean `total`?
slips.dv:24:3: E-EFFECT: `loud` does `io` (read/write the console) but does not declare it
    fix: add `uses io` to `fn loud` — or move this out of a pure function
evidence.dv:3:14: E-EXAMPLE: example failed: `sign(0) == 1` — got 0, expected 1
```

Trust ledger:

```
$ dovod trust tests/bad/evidence.dv
  can:    read/write the console (io)
  cannot: read files; write files; read command-line arguments; read the clock; use randomness; network; run other programs

  function       status          examples  blocks  notes
  sign           partial         2/2       2/3     never ran: line 7 (if)
  fast_inv_sqrt  assumed         -         -       ASSUMED: "accurate to 1e-6 for x in 0.01..100; checked by hand against a table"
  norm           self-checked    1/1       1/1     rests on assumed: fast_inv_sqrt
  main           shell           -         -       effects: io; rests on assumed: fast_inv_sqrt
  taken on faith:
    - fast_inv_sqrt: "..."  -> affects: norm, main
```

### What "checked" means — and what it does not

Examples written by the same author as the code catch slips and forgotten cases. They do **not** catch a
misunderstood task: the same misunderstanding goes into the code and into its examples. So the ledger
calls such a function `self-checked`, not "correct". For an independent check, someone else (a human,
another model) writes a *witness* file that `use`s the code:

```
$ dovod trust examples/witness/rpn.dv --witness examples/witness/rpn_witness.dv


  function  status          examples  blocks  notes
  rpn       witness-failed  12/12     11/11   independent examples passed: 8/9

  independent examples that failed:
    examples/witness/rpn_witness.dv:52: example trapped: `w_overflow()` — integer overflow in + (at rpn.dv:31)
```

This is a demonstration of what a failing witness looks like, **not a bug found in glm's code**. `rpn` was
written by glm-5.3 in the exam below and meets its task (hidden tests 8/8). The witness file, including
the failing example, was written by Claude — so it is not an independent author — and that example adds a
requirement the task never stated ("overflow gives `none`"); trapping on overflow is normal Dovod behavior.
A real witness comes from someone else and checks what the task actually asks.

## Exam: a model that has never seen Dovod

`exam/exam.py`: the model gets only SPEC.md and a task, sees `dovod check` output, up to 5 attempts;
then hidden tests it never saw are run. glm-5.3 (cloud), 2026-11-16, 8 tasks (fizzbuzz, reverse words,
palindrome, mean, run-length encoding, bracket balance, RPN calculator, record inventory):
**8/8 passed `check`, 7 on the first attempt; hidden tests 43/43.** The one retry was a compiler wart
(a local variable named `out` was refused) — fixed since. The tasks turned out too easy to show where
the evidence rule pays off: glm's own examples caught 0 bugs because there were none. Harder tasks next.

## Review by glm-5.3

The language was reviewed by glm-5.3 as a technical co-author (2026-11-16). Confirmed by running its
counterexamples and fixed: examples that test nothing were accepted (now the mutant check, with glm's
acceptance sets in `tests/vacuous/`), a witness with no examples counted as a witness, `or_else`
evaluated its default eagerly, Float division by zero did not trap, `show` did not escape control bytes,
`parse_float` accepted `0x10`/`inf`, the list of traps in the spec was incomplete. Two of its findings were
withdrawn after discussion (random range, ledger dependencies).

## Use

Needs Python 3.10+ and a C compiler (`cc`: gcc or clang).

```
bin/dovod check examples/shapes.dv          # static checks + run all examples
bin/dovod run   examples/wordfreq.dv README.md
bin/dovod build examples/bench.dv -o bench  # native binary
bin/dovod trust tests/bad/evidence.dv       # the ledger
bin/dovod card  examples/wordfreq.dv        # interface only, to hand to another agent
bin/dovod spec                              # the whole language
tests/run.sh                                # compiler self-test (incl. ASan/UBSan/leak check)
```

The full language is in [SPEC.md](SPEC.md) — designed to be read once, in one context window.

## Speed

`examples/bench.dv` vs the same code in `examples/bench.py` (Python 3.13), Intel N100, one run, all Dovod checks (overflow, bounds, contracts) on:

| task                         | Dovod  | Python   |
|------------------------------|--------|----------|
| prime sieve to 5,000,000     | 25 ms  | 590 ms   |
| longest Collatz below 1e6    | 298 ms | 10061 ms |
| building 2M number strings   | 113 ms | 145 ms   |

## Status

Version 0.1, an experiment. Not in it yet: maps, generics, closures, namespaces, network/process effects, Unicode-aware text indexing (`Text` is indexed by bytes), proofs beyond examples. Related work that shares ideas: Vera (contracts + Z3 for LLM-written code), Hylo (mutable value semantics), Koka (effect types).

Implementation: `dovod/syntax.py` (lexer, parser) → `dovod/check.py` (types, effects, evidence rules) → `dovod/cgen.py` (C backend with ownership) → `cc`. ~2,600 lines.

Designed and built by Claude (Anthropic) at the request of [helgard-orlm](https://github.com/helgard-orlm).

## License

MIT
