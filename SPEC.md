# Dovod 0.1 — the whole language

Dovod ("довод": an argument, a piece of evidence) is a small compiled language designed for AI
agents to write and for humans to trust. This file is the complete specification; read it once and
you can write Dovod. Files end in `.dv`. The compiler emits C and builds a native binary.

## Why it looks the way it does

| An AI author…                                   | …so Dovod                                                                 |
|-------------------------------------------------|---------------------------------------------------------------------------|
| writes fast; checking is the bottleneck         | every pure function must carry examples; the compiler runs them           |
| can write tests that test nothing               | an example must call its own function; unreached branches are reported    |
| reads code in fragments                         | no globals, no references, no hidden effects: a signature tells all       |
| makes small slips (wrong name, missed case)     | no shadowing, no unused names, exhaustive `match`, no implicit conversion |
| hands code to a human who will not read it      | `dovod trust` prints what the program can touch and what is taken on faith|
| has never seen this language                    | one way to do each thing; the spec is this file                           |

## Program shape

```
use "other.dv"                       // include another file's items (path relative to this file)
const pi: Float = 3.14159            // constants: literal Int, Float, Bool or Text only

record Point { x: Float, y: Float }

enum Shape {
  Circle(r: Float)
  Rect(w: Float, h: Float)
  Dot
}

fn area(s: Shape) -> Float           // pure by default
  give out >= 0.0                    // postcondition; `out` is the result
  ex area(Rect(w: 2.0, h: 3.0)) == 6.0
  ex area(Dot) == 0.0
{
  match s {
    Circle(r) => { return pi * r * r }
    Rect(w, h) => { return w * h }
    Dot => { return 0.0 }
  }
}

fn main() uses io {                  // effects are declared; main takes nothing, returns nothing
  print("area: " + show(area(Circle(r: 1.0))))
}
```

Names: types and enum variants are `Capitalized`; everything else is `lower_case`. Comments: `// …`.
One statement per line. A line may continue after a binary operator or inside `( )` / `[ ]`.

## Types

`Int` (64-bit, overflow traps) · `Float` (64-bit) · `Bool` · `Text` (immutable UTF-8 bytes; `len`,
`slice`, `byte` count **bytes**) · `List[T]` · `Opt[T]` · records · enums.

There is no null, no references, no generics for user functions, no closures. A record or enum
cannot contain itself directly; nest through a list: `enum Tree { Leaf(v: Int), Node(kids: List[Tree]) }`.

**Everything is a value.** Assigning or passing copies (the compiler avoids real copies where it
can). Two names never alias the same data, so changing one thing never changes another.

## Evidence (the core rule)

Clauses sit between the signature and the body, one per line, any order:

- `ex <Bool expr>` — an example. Must call the function it belongs to. Usually `ex f(args) == expected`.
  All examples run at every `check`/`run`/`build`; a failing example is a compile error showing got/expected.
  **An example must be able to fail.** Each passing example is run twice more with `f`'s result (its
  outermost calls) replaced by a mutant: the zero value (`0`, `0.0`, `false`, `""`, `[]`, `none`, first
  variant) and a perturbed result (Int ±1, Float +1.0, `not`, Text + `"\x01"`, list + one element, Opt
  flipped, every field of a record/variant perturbed, a field-less variant → the next one). If the example
  passes under every mutant that changed the result, it pins nothing down: `E-VACUOUS`
  (`ex f(2) == f(2)`, `ex len([f(1)]) == 1`, `ex f(1) == 2 or true`). A mutant that cannot differ from the
  real result, or that never fires (f called only from inside another function), is not judged.
  A mutant run that traps (e.g. division by the zero mutant) is also not judged, so a tautology cannot
  hide behind a trap (`ex 100 / f(4) == 100 / f(4)` is still `E-VACUOUS`). An example must also actually
  run `f` — `ex true or f(1) == 2` is an error. By design this also flags symmetric examples like
  `ex abs(-5) == abs(5)` — write `ex abs(-5) == 5`.
  **Refutable is not strong:** `ex f(1) != 0` passes the rule but pins down almost nothing. The rule
  removes examples that cannot fail; how much an example proves is judged by people and by witnesses.
- `need <Bool expr>` — precondition over the parameters. Checked on every call.
- `give <Bool expr>` — postcondition; must mention `out`. Checked on every return.
- `assume "reason"` — "I could not example this; take it on faith because …". Allowed, but it is
  reported forever, together with every function that depends on it.
- `uses a, b` — effects (goes right after the signature).

Rules:
- A pure function with neither `ex` nor `assume` does not compile.
- A function with effects cannot have `ex` (examples must be repeatable). It is a **shell**: keep it
  thin and put logic into pure functions.
- `uses` must list exactly the effects the function performs, itself or through callees — no more, no less.
- Statuses in `dovod trust`: `self-checked` (its own examples pass and reach every block) · `partial`
  (some block never ran — the report names the line) · `assumed` · `shell` · `witnessed` /
  `witness-failed` (see below). A block that starts with `trap(...)` is not counted.
- **Limit, stated plainly:** examples written by the same author as the code catch slips and forgotten
  cases, but not a misunderstood task — the same misunderstanding goes into both. For that, give
  `dovod trust FILE --witness W.dv`: W.dv is written by someone else (a human, another model), starts
  with `use "FILE"`, and holds functions like `fn w1() -> Bool  ex w1() { return f(2) == 4 }`.
  Every function of FILE called directly from a W.dv function that has examples becomes `witnessed`
  (if it was `self-checked`) or `witness-failed` (if any of those examples fails); `assumed` and
  `partial` stay as they are, with the witness result in the notes.

Effects: `io` (console) · `fs_read` · `fs_write` · `env` (arguments) · `clock` · `rand`.
There is no other way to reach the outside world, so the effects of `main` are the whole truth
about what a program can do.

## Statements

```
let x = expr                 // immutable; type is inferred
let n: Opt[Int] = none       // annotate when it cannot be inferred (`none`, `[]`)
var total = 0                // mutable; must actually be changed somewhere, or use `let`
total = total + 1
p.x = 3.0                    // fields and elements of a `var` can be assigned
xs[i] = v
xs.push(v)                   // only on a `var` List
xs.pop()                     // removes the last element; traps if empty

if cond { … } else if cond { … } else { … }      // cond must be Bool
while cond { … }
for i in 0..n { … }          // i = 0, 1, …, n-1
for x in xs { … }            // iterates a snapshot: changing xs inside is safe
break / continue

match value {                // only for enums and Opt; must cover every case
  Circle(r) => { … }         // bindings are FIELD NAMES of the variant, any subset, any order
  Rect(w, h) => { … }
  Dot => { … }
}
match find(t, "=") {
  some(i) => { … }
  none => { … }
}

return expr                  // every path of a non-Void function must return (or trap)
trap("message")              // stop the program: for states that cannot happen
f(x)                         // a call as a statement — only if it returns nothing
```

Strictness (all are compile errors, each comes with a `fix:` hint):
a name may not be reused while an outer one is visible (no shadowing; `out` may be a local name, not a parameter) · unused variables and
parameters (prefix with `_` to keep one) · `var` never changed · ignoring a returned value
(`let _ok = write_file(…)` to discard on purpose) · code after `return`/`break` · non-Bool conditions ·
mixing `Int` and `Float` (use `float(i)`, `trunc(f)`).

Parameters, `let` names, loop variables and match bindings are immutable. To change a parameter's
data, copy it: `var mine = param`.

## Expressions

Literals: `42` `3.5` `true` `false` `"text\n"` (escapes `\n \t \r \\ \" \0 \xNN` with NN in 00..7F) `[1, 2, 3]` `none` `some(x)`.
Constructors always name their fields: `Point(x: 1.0, y: 2.0)`, `Rect(w: 2.0, h: 3.0)`, `Dot`.
Access: `p.x`, `xs[i]` (out of range traps).

Operators, lowest to highest: `or` · `and` · `not` · `== != < <= > >=` (do not chain) · `+ -` ·
`* / %` · unary `-`. `and`/`or` are lazy. `+` also joins two `Text` or two lists of the same type.
`==` works structurally on every type. `<` works on `Int`, `Float`, `Text`.
Integer `/` truncates; division by zero (Int or Float) and Int overflow trap. Float overflow gives
infinity (IEEE), it does not trap. `or_else(o, d)` computes `d` only when `o` is `none`.

A typed hole `?name` may stand for any expression: the checker reports the type expected there and
the names in scope. Use it to ask the compiler what is needed.

## Built-in functions (the whole standard library)

```
len(Text|List) -> Int                 show(any) -> Text            // show(5)="5", show("a")="\"a\""
slice(Text|List, from, to) -> same    or_else(Opt[T], T) -> T
abs(n) min(a, b) max(a, b)            // Int or Float
float(Int) -> Float   trunc(Float) -> Int   sqrt(Float) -> Float   near(a, b, eps) -> Bool
find(t, sub) -> Opt[Int]     contains(t, sub) -> Bool
starts_with(t, p) -> Bool    ends_with(t, p) -> Bool
split(t, sep) -> List[Text]  join(List[Text], sep) -> Text
trim(t) -> Text              replace(t, from, to) -> Text
byte(t, i) -> Int            from_byte(Int) -> Text
parse_int(t) -> Opt[Int]     parse_float(t) -> Opt[Float]

print(Text)                          uses io        // adds a newline
read_line() -> Opt[Text]             uses io        // none at end of input
read_file(path) -> Opt[Text]         uses fs_read   // none if it cannot be read
write_file(path, text) -> Bool       uses fs_write
args() -> List[Text]                 uses env
now_ms() -> Int                      uses clock
rand_int(lo, hi) -> Int              uses rand      // inclusive
```

## Tools

```
dovod check FILE [--json]   static checks + run all examples. Diagnostics: file:line:col, code, msg, fix.
dovod run FILE [ARGS…]      check, compile (-O2), run
dovod build FILE [-o OUT]   check, compile to a native binary
dovod trust FILE [--witness W.dv] [--json]
                            the ledger: capabilities, per-function status, what is taken on faith
dovod card FILE             interface only: signatures, contracts, two examples each — hand this to
                            another agent instead of the source
dovod spec                  this file
```

Workflow for an agent: write the signature and the examples first, put `?todo` in the body, run
`check`, read the diagnostics (`--json` for machines), fill in, repeat until `OK`; then read
`trust` and remove every `partial` and every avoidable `assume`.

## Not in 0.1 (deliberately small)

Maps, generics, closures, modules with namespaces, Unicode-aware text indexing, a formatter, network
and process effects, proofs beyond examples. Known gap: calls made only inside `ex`/`need`/`give` are not
counted in the ledger's "rests on assumed".

Runtime traps (the complete list): failed `need`/`give` · Int overflow (`+ - *`, unary `-`, `abs`) ·
division by zero (`/`, `%`; Int and Float) · index/slice out of range (`xs[i]`, `byte`, `slice`) ·
`pop` on an empty list · `trunc` of a Float outside Int range · `from_byte` outside 0..255 ·
`split`/`replace` with an empty pattern · `rand_int` with lo > hi · out of memory · `trap(...)`.
