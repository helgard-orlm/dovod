"""Dovod command line: check | run | build | trust | card | spec."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .syntax import parse, Diag, DvError
from .check import Checker, tystr
from .cgen import CGen

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def module_dirs(root_path):
    return [os.path.join(os.path.dirname(os.path.abspath(root_path)), 'modules'), os.path.join(HERE, 'modules')]


def find_module(name, root_path):
    for d in module_dirs(root_path):
        f = os.path.join(d, name, name + '.dv')
        if os.path.isfile(f):
            return f
    return None


def load(path):
    """Parse `path` and everything it `use`s (files and modules).
    Returns (items, modules): every item carries `.module` (a module name or None);
    modules = {name: {'file', 'dir', 'effects': {name: doc}, 'native', 'targets', 'links', 'gui', 'root'}}."""
    items, seen, modules = [], set(), {}

    def one(p, shown, at=None, want_module=None):
        real = os.path.realpath(p)
        if real in seen:
            return
        seen.add(real)
        try:
            src = open(p, encoding='utf-8').read()
        except OSError as ex:
            f, l, c = (at.file, at.line, at.col) if at else (shown, 1, 1)
            raise DvError(Diag('E-FILE', f, l, c, f"cannot read `{shown}`: {ex.strerror}"))
        parsed = parse(src, shown)
        mod = None
        if parsed and parsed[0].k == 'Module':
            mod = parsed[0].name
            if want_module is not None and mod != want_module:
                m = parsed[0]
                raise DvError(Diag('E-MODULE', m.file, m.line, m.col,
                                   f"this file says `module {mod}` but it was found as module `{want_module}`"))
            if mod in modules:
                m = parsed[0]
                raise DvError(Diag('E-MODULE', m.file, m.line, m.col, f"module `{mod}` is defined twice"))
            modules[mod] = {'file': shown, 'dir': os.path.dirname(p), 'effects': {}, 'native': None, 'targets': [],
                            'links': [], 'gui': False, 'root': at is None, 'doc': leading_comment(src)}
        elif want_module is not None:
            raise DvError(Diag('E-MODULE', shown, 1, 1, f"a module file must start with `module {want_module}`"))
        for it in parsed[1:] if mod else parsed:
            if it.k in ('Module', 'Effect', 'Native') or (it.k == 'Fn' and it.extern):
                if it.k == 'Module' or mod is None:
                    what = {'Module': '`module`', 'Effect': '`effect`', 'Native': '`native`', 'Fn': '`extern fn`'}[it.k]
                    raise DvError(Diag('E-MODULE', it.file, it.line, it.col,
                                       f"{what} is allowed only in a module file (one that starts with `module NAME`)",
                                       "programs reach native code only through a module: `use NAME`"))
            if it.k == 'Use':
                if it.module:
                    mf = find_module(it.module, path)
                    if mf is None:
                        have = sorted(set(n for d in module_dirs(path) if os.path.isdir(d) for n in os.listdir(d)
                                          if os.path.isfile(os.path.join(d, n, n + '.dv'))))
                        raise DvError(Diag('E-MODULE', it.file, it.line, it.col, f"no module `{it.module}`",
                                           "modules: " + (', '.join(have) or 'none') + " (looked in ./modules and the built-in ones)"))
                    one(mf, os.path.relpath(mf), it, want_module=it.module)
                else:
                    sub = os.path.join(os.path.dirname(p), it.path)
                    one(sub, os.path.join(os.path.dirname(shown), it.path), it)
                continue
            it.module = mod
            if it.k == 'Effect':
                modules[mod]['effects'][it.name] = it.doc
            elif it.k == 'Native':
                m = modules[mod]
                if m['native']:
                    raise DvError(Diag('E-MODULE', it.file, it.line, it.col, "a module has one `native` file"))
                m['native'] = os.path.join(m['dir'], it.path)
                if not os.path.isfile(m['native']):
                    raise DvError(Diag('E-FILE', it.file, it.line, it.col, f"native file `{it.path}` not found next to the module"))
                bad = [t for t in it.targets + [t for _, only in it.links for t in (only or [])] if t not in TARGETS]
                if bad:
                    raise DvError(Diag('E-MODULE', it.file, it.line, it.col, f"unknown target `{bad[0]}`",
                                       "targets: " + ', '.join(TARGETS)))
                m['targets'], m['links'], m['gui'] = it.targets or list(TARGETS), it.links, it.gui
            items.append(it)

    one(path, path)
    return items, modules


def leading_comment(src):
    out = []
    for ln in src.splitlines():
        if ln.startswith('//'):
            out.append(ln[2:].lstrip(' ') if len(ln) > 2 else '')
        elif ln.strip() == '' and not out:
            continue
        else:
            break
    return '\n'.join(out)


TARGETS = {'native': 'cc', 'win64': 'x86_64-w64-mingw32-gcc', 'win32': 'i686-w64-mingw32-gcc'}
WIN_LIBS = ['-lshell32']


def compile_c(csrc, out, opt, target='native', gui=False, links=()):
    cdir = os.path.dirname(out)
    cfile = os.path.join(cdir, os.path.basename(out) + '.c')
    with open(cfile, 'w') as f:
        f.write(csrc)
    extra = os.environ.get('DOVOD_CFLAGS', '').split()
    cc = TARGETS[target]
    if shutil.which(cc) is None:
        raise SystemExit(f"target {target} needs the C compiler `{cc}` (Debian/Parrot: apt install mingw-w64)")
    libs = ['-lm']
    if target != 'native':
        extra = extra + ['-static', '-s']   # one .exe, no MinGW DLLs next to it
        libs = libs + WIN_LIBS + (['-mwindows'] if gui else [])
    libs = libs + list(links)
    r = subprocess.run([cc, '-std=gnu11', opt, '-w'] + extra + ['-o', out, cfile] + libs,
                       capture_output=True, text=True)
    if r.returncode != 0:
        keep = os.path.join(tempfile.gettempdir(), 'dovod_failed.c')
        shutil.copy(cfile, keep)
        raise SystemExit(f"internal error: generated C did not compile (this is a Dovod bug, kept at {keep})\n"
                         + r.stderr[:3000])


def big_stack():
    """Examples run under mutants may recurse far deeper than the real code; give them room."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
        want = 1 << 30
        if hard != resource.RLIM_INFINITY:
            want = min(want, hard)
        resource.setrlimit(resource.RLIMIT_STACK, (want, hard))
    except Exception:
        pass


class Result:
    def __init__(self):
        self.diags, self.warns, self.fns = [], [], []
        self.ck = None
        self.ok = False
        self.bm_partial = False


def analyse(path, run_examples=True):
    """Static check, then (if clean) compile and run every example with coverage."""
    res = Result()
    try:
        items, modules = load(path)
    except DvError as ex:
        res.diags.append(ex.diag)
        return res
    ck = Checker(items, modules)
    res.ck = ck
    res.diags = ck.run()
    if res.diags:
        return res
    rep = {}
    for f in ck.fns.values():
        n = f.node
        kind = 'shell' if (f.uses or f.name == 'main') else ('assumed' if n.assume else 'self-checked')
        rep[f.name] = {'name': f.name, 'status': kind, 'uses': list(f.uses), 'examples': len(n.exs), 'passed': 0,
                       'blocks': 0, 'hit': 0, 'unreached': [], 'assume': n.assume[0] if n.assume else None,
                       'line': n.line, 'lines': n.end_line - n.line + 1, 'file': n.file,
                       'mutants': 0, 'killed': 0, 'weak': [],
                       'module': n.module if (n.module and not ck.modules[n.module]['root']) else None}
        if n.extern:
            rep[f.name]['status'] = 'extern'
    if run_examples:
        gen = CGen(ck, 'test', 'native')
        csrc = gen.program()
        tmp = tempfile.mkdtemp(prefix='dovod_')
        try:
            exe = os.path.join(tmp, 'test')
            compile_c(csrc, exe, '-O0')
            try:
                out = subprocess.run([exe], capture_output=True, text=True, timeout=60,
                                     preexec_fn=big_stack if os.name == 'posix' else None)
                lines, crashed = out.stdout.splitlines(), out.returncode != 0
                why = f"crashed (exit code {out.returncode}; e.g. stack overflow from deep recursion)"
            except subprocess.TimeoutExpired as ex:
                lines = (ex.stdout or b'').decode('utf-8', 'replace').splitlines()
                crashed = True
                why = "did not finish within 60 s (endless loop or very slow code)"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        done = set()
        for ln in lines:
            p = ln.split('\t')
            if p[0] == 'EX':
                i = int(p[1])
                done.add(i)
                fn, e = gen.exs[i]
                if p[2] == 'ok':
                    rep[fn]['passed'] += 1
                elif p[2] == 'fail':
                    msg = f"example failed: `{e.src}`"
                    if len(p) >= 5:
                        msg += f" — got {p[3]}, expected {p[4]}"
                    res.diags.append(Diag('E-EXAMPLE', e.file, e.line, e.col, msg,
                                          f"fix `{fn}` (or the example, if the example is what is wrong)"))
                else:
                    res.diags.append(Diag('E-EXAMPLE', e.file, e.line, e.col,
                                          f"example trapped: `{e.src}` — {p[3] if len(p) > 3 else ''}"))
            elif p[0] == 'NORUN':
                fn, e = gen.exs[int(p[1])]
                res.diags.append(Diag('E-EVIDENCE', e.file, e.line, e.col,
                                      f"this example mentions `{fn}` but never runs it (`and`/`or` skipped the call): `{e.src}`",
                                      f"make the call to `{fn}` unconditional: `ex {fn}(...) == <value>`"))
                rep[fn]['vacuous'] = rep[fn].get('vacuous', 0) + 1
            elif p[0] == 'MUT':
                fired, survived = int(p[2]), int(p[3])
                if fired and survived == fired:
                    fn, e = gen.exs[int(p[1])]
                    res.diags.append(Diag('E-VACUOUS', e.file, e.line, e.col,
                                          f"this example also passes when `{fn}` returns a different value "
                                          f"(zero or a perturbed result), so it does not pin down `{fn}`: `{e.src}`",
                                          f"compare the result with a concrete expected value: `ex {fn}(...) == <value>`"))
                    rep[fn]['vacuous'] = rep[fn].get('vacuous', 0) + 1
            elif p[0] == 'COV':
                c = gen.cov[int(p[1])]
                c['count'] = int(p[2])
            elif p[0] == 'BM':
                m = gen.bmuts[int(p[1]) - 1]
                if int(p[3]):
                    r = rep[m['fn']]
                    r['mutants'] += 1
                    if int(p[2]):
                        r['killed'] += 1
                    else:
                        r['weak'].append(m)
            elif p[0] == 'BMSTOP':
                res.bm_partial = True
        if crashed and len(done) == len(gen.exs):
            res.bm_partial = True   # every example ran; the crash/timeout was in the strength phase
        if crashed:
            for i, (fn, e) in enumerate(gen.exs):
                if i not in done:
                    res.diags.append(Diag('E-EXAMPLE', e.file, e.line, e.col,
                                          f"example {why}: `{e.src}`"))
                    break
        for c in gen.cov:
            r = rep[c['fn']]
            if r['status'] == 'shell' or c['skip']:
                continue
            r['blocks'] += 1
            if c.get('count', 0) > 0:
                r['hit'] += 1
            else:
                r['unreached'].append((c['line'], c['kind']))
        for r in rep.values():
            if r['status'] == 'self-checked':
                if r['passed'] < r['examples'] or r.get('vacuous'):
                    r['status'] = 'failed'
                elif r['hit'] < r['blocks']:
                    r['status'] = 'partial'
                    for line, kind in r['unreached']:
                        res.warns.append(Diag('W-UNREACHED', r['file'], line, 1,
                                              f"no example ever runs this `{kind}` block of `{r['name']}`",
                                              "add an `ex` that reaches it"))
    # transitive: which assumptions / untested code does each function rest on
    for f in ck.fns.values():
        seen, stack = set(), [f.name]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(ck.fns[x].calls)
        rep[f.name]['rests_on'] = sorted(x for x in seen if rep[x]['assume'] and x != f.name)
    res.fns = [rep[f] for f in ck.fns]
    for d in ck.late:
        res.diags = [x for x in res.diags if (x.file, x.line, x.col) != (d.file, d.line, d.col)]
        res.diags.append(d)
        for f in res.fns:
            if f['file'] == d.file and f['line'] <= d.line <= f['line'] + f['lines'] and f['status'] != 'shell':
                f['status'] = 'failed'
    res.diags.sort(key=lambda d: (d.file, d.line, d.col))
    res.ok = not res.diags
    return res


def report(res, as_json, quiet_ok=False):
    if as_json:
        print(json.dumps({'ok': res.ok, 'errors': [d.to_dict() for d in res.diags],
                          'warnings': [d.to_dict() for d in res.warns],
                          'functions': [{k: ([{x: m[x] for x in ('line', 'col', 'what')} for m in v] if k == 'weak' else v)
                                         for k, v in f.items() if k not in ('file',)} for f in res.fns]},
                         ensure_ascii=False, indent=1))
        return
    for d in res.diags:
        print(d.text(), file=sys.stderr)
    for d in res.warns:
        print(d.text(), file=sys.stderr)
    if res.diags:
        print(f"FAILED: {len(res.diags)} error(s)", file=sys.stderr)
    elif not quiet_ok:
        own = [f for f in res.fns if not f['module']]
        ex = sum(f['passed'] for f in own)
        print(f"OK: {len(own)} function(s), {ex} example(s) passed"
              + (f", {len(res.warns)} warning(s)" if res.warns else ''))
        for line in module_lines(res):
            print(line)
        mt, kl = sum(f['mutants'] for f in own), sum(f['killed'] for f in own)
        if mt:
            print(f"strength: the examples notice {kl} of {mt} small changes to the code"
                  + (f" — `dovod weak` lists the {mt - kl} they miss" if kl < mt else '')
                  + (" (time limit hit: not all changes were tried)" if res.bm_partial else ''))


def manifest(ck):
    used = []
    if 'main' in ck.fns:
        used = ck.fns['main'].uses
    else:
        for f in ck.fns.values():
            for u in f.uses:
                if u not in used:
                    used.append(u)
    return used


def apply_witness(res, wpath):
    """Run a witness file (examples by another author that `use` this code). Functions it calls
    directly become `witnessed` (all its examples pass) or `witness-failed`."""
    w = analyse(wpath)
    if w.ck is None or any(d.code != 'E-EXAMPLE' for d in w.diags):
        report(w, False)
        raise SystemExit(f"witness file {wpath} does not check (see above)")
    wfile = os.path.normpath(wpath)
    own = {f['name']: f for f in res.fns}
    wfns = [f for f in w.ck.fns.values() if os.path.normpath(f.node.file) == wfile]
    failed_lines = [d.line for d in w.diags if os.path.normpath(d.file) == wfile]
    hits = {}
    for f in wfns:
        bad = any(f.node.line <= ln <= f.node.end_line for ln in failed_lines)
        n_ex = len(f.node.exs)
        if n_ex == 0:
            continue
        for callee in f.calls:
            if callee in own:
                h = hits.setdefault(callee, [0, 0])
                h[0] += n_ex
                h[1] += 0 if bad else n_ex
    for name, (total, ok) in hits.items():
        f = own[name]
        f['witness'] = f"{ok}/{total}"
        if ok < total:
            f['status'] = 'witness-failed'
        elif f['status'] == 'self-checked':
            f['status'] = 'witnessed'
    return w


def cmd_trust(res, path, witness=None):
    ck = res.ck
    used = manifest(ck)
    print(f"TRUST LEDGER  {path}")
    print("  can:    " + ('; '.join(f"{ck.effect_doc[u]} ({u})" for u in used) or "nothing but compute"))
    print("  cannot: " + '; '.join(ck.effect_doc[u] for u in ck.effects if u not in used)
          + "; network; run other programs")
    print()
    shown = [f for f in res.fns if not f['module']]
    w = max([len('function')] + [len(f['name']) for f in shown])
    print(f"  {'function'.ljust(w)}  status          examples  blocks  strength  notes")
    for f in shown:
        st = f['status']
        exs = f"{f['passed']}/{f['examples']}" if st not in ('shell', 'assumed') or f['examples'] else '-'
        bl = f"{f['hit']}/{f['blocks']}" if f['blocks'] else '-'
        notes = []
        if f['assume']:
            notes.append(f"ASSUMED: \"{f['assume']}\"")
        if st == 'shell':
            notes.append("effects: " + (', '.join(f['uses']) or 'none'))
        if st == 'extern':
            notes.append("native code" + (", effects: " + ', '.join(f['uses']) if f['uses'] else ''))
        if f['unreached']:
            notes.append("never ran: " + ', '.join(f"line {l} ({k})" for l, k in f['unreached']))
        if f.get('witness'):
            notes.append(f"independent examples passed: {f['witness']}")
        if f['rests_on']:
            notes.append("rests on assumed: " + ', '.join(f['rests_on']))
        sg = f"{f['killed']}/{f['mutants']}" if f['mutants'] else '-'
        print(f"  {f['name'].ljust(w)}  {st.ljust(14)}  {exs.ljust(8)}  {bl.ljust(6)}  {sg.ljust(8)}  {'; '.join(notes)}")
    cnt = {}
    for f in shown:
        cnt[f['status']] = cnt.get(f['status'], 0) + 1
    total = sum(f['lines'] for f in shown)
    shell = sum(f['lines'] for f in shown if f['status'] == 'shell')
    for line in module_lines(res):
        print("  " + line)
    print()
    if witness is not None and witness.diags:
        print("  independent examples that failed:")
        for d in witness.diags:
            print(f"    {d.file}:{d.line}: {d.msg}")
        print()
    print("  self-checked = agrees with its author's own examples: catches slips, not a misread task.")
    print("  strength     = how many small changes to the body (a number +1, `<` to `<=`, `+` to `-`, a removed")
    print("                 assignment...) make some example fail. `dovod weak FILE` lists the ones nothing notices.")
    if res.bm_partial:
        print("                 NOTE: the strength phase was cut short (time limit or a crash under a changed body):")
        print("                 not every change was tried, the strength numbers above are incomplete.")
    print("  witnessed    = also passed examples written by someone else (--witness FILE).")
    print("  summary: " + ', '.join(f"{v} {k}" for k, v in cnt.items())
          + f". Unchecked shell = {shell} of {total} function lines ({100 * shell // max(total, 1)}%).")
    assumed = [f for f in res.fns if f['assume']]
    if assumed:
        print("  taken on faith:")
        for f in assumed:
            deps = [g['name'] for g in res.fns if f['name'] in g['rests_on']]
            print(f"    - {f['name']}: \"{f['assume']}\"" + (f"  -> affects: {', '.join(deps)}" if deps else ''))
    else:
        print("  taken on faith: nothing.")
    print("  outside this ledger (no example reaches it):")
    for line in outside(ck):
        print("    - " + line)


def clines(name):
    try:
        return sum(1 for _ in open(name if os.path.isabs(name) else os.path.join(HERE, name), encoding='utf-8'))
    except OSError:
        return 0


def module_lines(res):
    """One line per module the program uses: what in it is native (unchecked) and what is exampled Dovod."""
    out = []
    for name, m in res.ck.modules.items():
        if m['root']:
            continue
        fs = [f for f in res.fns if f['module'] == name]
        ext = sum(1 for f in fs if f['status'] == 'extern')
        dv = [f for f in fs if f['status'] != 'extern']
        s = f"module {name}: {ext} native function(s)"
        if m['native']:
            s += f" ({clines(os.path.abspath(m['native']))} lines of C, targets: {', '.join(m['targets'])})"
        if dv:
            mt, kl = sum(f['mutants'] for f in dv), sum(f['killed'] for f in dv)
            s += f"; {len(dv)} Dovod function(s), {sum(f['passed'] for f in dv)} example(s) passed"
            if mt:
                s += f", strength {kl}/{mt}"
        out.append(s)
    return out


def outside(ck):
    """What the examples can never vouch for: code that is not Dovod."""
    out = [f"Dovod's C runtime under every program (text, lists, traps): {clines('rt_pre.h') + clines('rt_post.h')} lines of C"]
    for name, m in ck.modules.items():
        ext = [f.name for f in ck.fns.values() if f.node.extern and f.node.module == name]
        if ext:
            n = clines(os.path.abspath(m['native'])) if m['native'] else 0
            what = '; '.join(m['effects'].values()) or 'native code'
            out.append(f"module `{name}`: {len(ext)} native function(s), {n} lines of C — {what}. "
                       "What that code really does is NOT checked here")
    shells = [f.name for f in ck.fns.values() if (f.uses or f.name == 'main') and not f.node.extern
              and not (f.node.module and not ck.modules[f.node.module]['root'])]
    if shells:
        out.append("the shell functions themselves (" + ', '.join(shells) + "): they run only in the real program")
    out.append("whether the task was understood: examples by the code's author share the author's reading of it")
    return out


def cmd_weak(res):
    n = 0
    own = [f for f in res.fns if not f['module']]
    for f in own:
        for m in f['weak']:
            n += 1
            print(f"{m['file']}:{m['line']}:{m['col']}: W-WEAK: in `{f['name']}` no example notices if {m['what']}")
    mt, kl = sum(f['mutants'] for f in own), sum(f['killed'] for f in own)
    print(f"{kl} of {mt} small changes are noticed by the examples; {n} are not."
          + (" Time limit hit: not all were tried." if res.bm_partial else ''))
    if n:
        print("Each line above is a place where the code could be wrong and every example would still pass.\n"
              "Add an example that pins it down — or leave it if the change cannot matter (e.g. `<` vs `<=` where both give the same result).")


def cmd_card(ck, path):
    print(f"// card of {path}: everything needed to call this code without reading its bodies")
    for it in ck.items:
        if it.module and not ck.modules[it.module]['root']:
            continue
        if it.k == 'Effect':
            print(f"effect {it.name}  // {it.doc}")
        elif it.k == 'Record':
            print(f"record {it.name} {{ " + ', '.join(f"{n}: {tystr(t)}" for n, t, _ in it.fields) + " }")
        elif it.k == 'Enum':
            vs = [v + (f"({', '.join(f'{n}: {tystr(t)}' for n, t, _ in fs)})" if fs else '') for v, fs, _ in it.variants]
            print(f"enum {it.name} {{ " + ', '.join(vs) + " }")
        elif it.k == 'Const':
            print(f"const {it.name}: {tystr(it.ty)}")
        elif it.k == 'Fn' and it.extern:
            try:
                src = open(it.file, encoding='utf-8').read().splitlines()
                print('\n'.join(src[it.line - 1:it.end_line]))
            except OSError:
                print(it.sig)
        elif it.k == 'Fn' and it.name != 'main':
            print(it.sig)
            if it.uses:
                print("  uses " + ', '.join(u for u, _ in it.uses))
            for e in it.needs:
                print("  need " + e.src)
            for e in it.gives:
                print("  give " + e.src)
            for e in it.exs[:2]:
                print("  ex " + e.src)
            if it.assume:
                print(f"  assume \"{it.assume[0]}\"")


def build(res, out, opt='-O2', target='native'):
    if 'main' not in res.ck.fns:
        raise SystemExit("no `fn main()` in this program; nothing to run (use `dovod check` for libraries)")
    csrc = CGen(res.ck, 'run', target).program()
    mods = [m for m in res.ck.modules.values() if m['native'] and target in m['targets']]
    compile_c(csrc, out, opt, target, any(m['gui'] for m in mods),
              [l for m in mods for l, only in m['links'] if only is None or target in only])
    try:
        os.remove(out + '.c')
    except OSError:
        pass


USAGE = """dovod — a language where code carries its evidence
  dovod check FILE [--json]     type-check, run every example, report
  dovod run   FILE [ARGS...]    check, compile, run
  dovod build FILE [-o OUT] [--target native|win64|win32]
                                check, compile to a binary (win64/win32: a Windows .exe via MinGW)
  dovod trust FILE [--witness W.dv] [--json]
                                ledger: what the program can do, what is checked, what is assumed;
                                W.dv = examples by another author (`use`s FILE)
  dovod weak  FILE              places where the code could change and no example would notice
  dovod card  FILE              interface only (signatures, contracts, examples) for another agent
  dovod spec                    print the whole language spec (fits in one context window)
  dovod modules                 list the modules (`use NAME`); `dovod spec NAME` prints a module's page,
                                `dovod spec modules` — how to write one
"""


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ('-h', '--help', 'help'):
        print(USAGE)
        return 0
    cmd, a = a[0], a[1:]
    if cmd == 'spec':
        if not a:
            print(open(os.path.join(ROOT, 'SPEC.md'), encoding='utf-8').read())
            return 0
        if a[0] == 'modules':
            print(open(os.path.join(ROOT, 'MODULES.md'), encoding='utf-8').read())
            return 0
        mf = find_module(a[0], os.path.join(os.getcwd(), 'x.dv'))
        if mf is None:
            print(f"no module `{a[0]}` (see `dovod modules`)", file=sys.stderr)
            return 1
        res = analyse(mf, run_examples=False)
        if res.diags:
            report(res, False)
            return 1
        m = res.ck.modules[a[0]]
        print(f"# Dovod module `{a[0]}` — use it with `use {a[0]}`\n")
        print(m['doc'] + "\n")
        print("native code: " + (f"{os.path.basename(m['native'])}, targets {', '.join(m['targets'])}"
                                 + (" (other targets: calls trap; pure code still checks)" if len(m['targets']) < len(TARGETS) else '')
                                 if m['native'] else "none — this module is pure Dovod") + "\n")
        cmd_card(res.ck, mf)
        return 0
    if cmd == 'modules':
        seen = set()
        for d in module_dirs(os.path.join(os.getcwd(), 'x.dv')):
            for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                f = os.path.join(d, n, n + '.dv')
                if os.path.isfile(f) and n not in seen:
                    seen.add(n)
                    doc = leading_comment(open(f, encoding='utf-8').read()).split('\n')[0]
                    print(f"{n.ljust(10)} {doc}")
        print("\n`dovod spec NAME` prints a module's page; `dovod spec modules` — how to write one.")
        return 0
    if cmd not in ('check', 'run', 'build', 'trust', 'card', 'weak') or not a:
        print(USAGE, file=sys.stderr)
        return 2
    as_json = '--json' in a
    if cmd != 'run':
        a = [x for x in a if x != '--json']
    path = a[0]
    if cmd == 'card':
        res = analyse(path, run_examples=False)
        if res.diags:
            report(res, False)
            return 1
        cmd_card(res.ck, path)
        return 0
    res = analyse(path)
    if cmd == 'check':
        report(res, as_json)
        return 0 if res.ok else 1
    if cmd == 'weak':
        if not res.ok:
            report(res, False)
            return 1
        cmd_weak(res)
        return 0
    if cmd == 'trust':
        wit = None
        if res.ok and '--witness' in a:
            wit = apply_witness(res, a[a.index('--witness') + 1])
        if as_json or not res.ok:
            report(res, as_json)
            return 0 if res.ok else 1
        cmd_trust(res, path, wit)
        return 0
    if not res.ok:
        report(res, False)
        return 1
    for d in res.warns:
        print(d.text(), file=sys.stderr)
    if cmd == 'build':
        target = a[a.index('--target') + 1] if '--target' in a else 'native'
        if target not in TARGETS:
            raise SystemExit(f"unknown target `{target}`; targets: " + ', '.join(TARGETS))
        out = a[a.index('-o') + 1] if '-o' in a else os.path.splitext(os.path.basename(path))[0]
        if target != 'native' and not out.endswith('.exe'):
            out += '.exe'
        build(res, os.path.abspath(out), target=target)
        print(f"built {out}")
        return 0
    tmp = tempfile.mkdtemp(prefix='dovod_')
    try:
        exe = os.path.join(tmp, 'prog')
        build(res, exe)
        return subprocess.run([exe] + a[1:]).returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
