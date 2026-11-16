# Dovod modules — how the language grows without the core growing

The core (SPEC.md) stays one page. Everything task-specific — a window, a device, a network — is a
**module**: functions and types behind an effect, with its native code and its own page.
`use video` at the top of a program brings a module in; `dovod modules` lists them; `dovod spec NAME`
prints a module's page (generated from the module file itself, so it cannot go stale).

**A module cannot add syntax or change a rule of the language.** It adds names. A reader who knows the
core can read any program; for the names of a module they read that one page.

## A module is a folder

```
modules/NAME/NAME.dv     the interface: ordinary Dovod plus the four words below
modules/NAME/NAME.h      native code (C), optional
```
Looked up first in `modules/` next to the program, then among the built-in ones (`dovod/modules/`).

```
// The leading comment is the module's page: say what it is for and how a program should use it.
module relay                                   // first line of code; same as the folder name
effect relay "switch the mains relay"          // the effect(s) this module brings; shown in `trust`
native "relay.h"                               // at most one native file
  targets native, win64                        // where that code exists (default: every target)
  links "-lws2_32" on win64                    // extra linker flags (`on ...` = only for these targets)
  gui                                          // Windows: build as a window program, not a console one

extern fn relay_set(on: Bool) uses relay       // body = C function `dv_relay_set` in relay.h
extern fn relay_level(pct: Int) -> Int uses relay
  need pct >= 0 and pct <= 100                 // checked by generated code before the C runs

enum RelayState { RelayOn, RelayOff, RelayUnknown(raw: Text) }

fn relay_parse(raw: Text) -> RelayState        // ordinary Dovod: examples, strength, all the rules
  ex relay_parse("1") == RelayOn
  ...
```

## Rules (the checker enforces all of them)

1. **Names instead of namespaces.** Every function and constant of module `relay` starts with `relay_`;
   every type and variant with `Relay`. A program (or another module) may not declare a name that
   starts that way. So the origin of any name is visible in the name.
2. **`module`, `effect`, `native`, `extern` exist only in module files.** A program reaches native code
   only through `use NAME`, so the effects of `main` are still the whole truth about what it can touch.
   A module may `use` other modules; effects travel up by the normal rule (`uses` lists exactly what a
   function does, itself or through callees).
3. **Only simple values cross the border.** `extern` parameters and results are `Int`, `Float`, `Bool`,
   `Text`, `Opt[Int|Float|Text]`, `List[Text]`. Anything richer is built in Dovod on top — parse a `Text`
   into an enum, serialise a record tree into a `Text` — where it can carry examples. (On a future
   no-heap target: only `Int`, `Float`, `Bool`.)
4. **`extern` carries `uses`, may carry `need`, never `give` or `ex`.** A pure `extern` is allowed but is
   reported as `assumed: native code` forever, with everything that rests on it.
5. **Wrong target → trap, not a build error.** If the module has no native code for the build target the
   compiler generates stubs that trap with a clear message. Pure code and all examples still check on
   any machine — that is how a Windows program is developed on Linux.
6. The ledger lists each module on one line and, under "outside this ledger", how many lines of native
   code nobody examples. No module versions: a module lives with the program or with the compiler.

## The native side (C)

- `extern fn NAME(...)` is the C function `static RET dv_NAME(args)`. Types: `int64_t`, `double`, `bool`,
  `dv_text {char* p; int64_t n}` (UTF-8 bytes, **not** NUL-terminated), `O_Int / O_Float / O_Text
  {bool has; T v}`, `L_Text {dv_text* p; int64_t n, cap}`.
- **Arguments are borrowed:** do not free them, do not keep pointers to them after returning (copy what
  you need). **Results are owned by the caller:** make them with `dv_text_new(ptr, n)`, `L_Text_push`.
- Report impossible states with `dv_trap("message", "where")`; report expected failures as values
  (`Bool`, `Opt`, an event).
- **Resources are Int handles**: return an id, keep the table in C.
- **The program owns the loop; native code owns nothing global.** Never block without a time limit and
  never run an event loop of your own. A module with events gives a pull function that waits a bounded
  time and returns ONE event as `Text` ("nothing" included); the module parses it into an enum in Dovod.
  Then a program can serve two modules by calling their pull functions in turn.
- **Keep C stateless where you can.** Prefer "here is the whole new state, draw it" to patch calls: state
  kept in C is state no example sees.

## Writing one

Start from the Dovod side: the enum of events, the parser with examples, the functions a program will
call. Put `extern` only where the outside world really begins. Run `dovod check modules/NAME/NAME.dv`
(a module file checks on its own), then `dovod spec NAME` and read the page as a stranger would.
