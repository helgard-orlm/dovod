"""Dovod command line: check | run | build | trust | card | spec."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .syntax import parse, Diag, DvError
from .check import Checker, EFFECTS, EFFECT_DOC, tystr
from .cgen import CGen

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(path):
    """Parse `path` and everything it `use`s. Returns (items, error diag or None)."""
    items, seen = [], set()

    def one(p, shown, at=None):
        real = os.path.realpath(p)
        if real in seen:
            return
        seen.add(real)
        try:
            src = open(p, encoding='utf-8').read()
        except OSError as ex:
            f, l, c = (at.file, at.line, at.col) if at else (shown, 1, 1)
            raise DvError(Diag('E-FILE', f, l, c, f"cannot read `{shown}`: {ex.strerror}"))
        for it in parse(src, shown):
            if it.k == 'Use':
                sub = os.path.join(os.path.dirname(p), it.path)
                one(sub, os.path.join(os.path.dirname(shown), it.path), it)
            else:
                items.append(it)

    one(path, path)
    return items


def compile_c(csrc, out, opt):
    cdir = os.path.dirname(out)
    cfile = os.path.join(cdir, os.path.basename(out) + '.c')
    with open(cfile, 'w') as f:
        f.write(csrc)
    extra = os.environ.get('DOVOD_CFLAGS', '').split()
    r = subprocess.run(['cc', '-std=gnu11', opt, '-w'] + extra + ['-o', out, cfile, '-lm'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        keep = os.path.join(tempfile.gettempdir(), 'dovod_failed.c')
        shutil.copy(cfile, keep)
        raise SystemExit(f"internal error: generated C did not compile (this is a Dovod bug, kept at {keep})\n"
                         + r.stderr[:3000])


class Result:
    def __init__(self):
        self.diags, self.warns, self.fns = [], [], []
        self.ck = None
        self.ok = False


def analyse(path, run_examples=True):
    """Static check, then (if clean) compile and run every example with coverage."""
    res = Result()
    try:
        items = load(path)
    except DvError as ex:
        res.diags.append(ex.diag)
        return res
    ck = Checker(items)
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
                       'line': n.line, 'lines': n.end_line - n.line + 1, 'file': n.file}
    if run_examples:
        gen = CGen(ck, 'test')
        csrc = gen.program()
        tmp = tempfile.mkdtemp(prefix='dovod_')
        try:
            exe = os.path.join(tmp, 'test')
            compile_c(csrc, exe, '-O0')
            try:
                out = subprocess.run([exe], capture_output=True, text=True, timeout=60)
                lines, crashed = out.stdout.splitlines(), out.returncode != 0
            except subprocess.TimeoutExpired as ex:
                lines = (ex.stdout or b'').decode('utf-8', 'replace').splitlines()
                crashed = True
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
            elif p[0] == 'COV':
                c = gen.cov[int(p[1])]
                c['count'] = int(p[2])
        if crashed:
            for i, (fn, e) in enumerate(gen.exs):
                if i not in done:
                    res.diags.append(Diag('E-EXAMPLE', e.file, e.line, e.col,
                                          f"example did not finish (crash, endless loop or stack overflow): `{e.src}`"))
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
                if r['passed'] < r['examples']:
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
        rep[f.name]['rests_on'] = sorted(x for x in seen if rep[x]['status'] == 'assumed' and x != f.name)
    res.fns = [rep[f] for f in ck.fns]
    res.ok = not res.diags
    return res


def report(res, as_json, quiet_ok=False):
    if as_json:
        print(json.dumps({'ok': res.ok, 'errors': [d.to_dict() for d in res.diags],
                          'warnings': [d.to_dict() for d in res.warns],
                          'functions': [{k: v for k, v in f.items() if k not in ('file',)} for f in res.fns]},
                         ensure_ascii=False, indent=1))
        return
    for d in res.diags:
        print(d.text(), file=sys.stderr)
    for d in res.warns:
        print(d.text(), file=sys.stderr)
    if res.diags:
        print(f"FAILED: {len(res.diags)} error(s)", file=sys.stderr)
    elif not quiet_ok:
        ex = sum(f['passed'] for f in res.fns)
        print(f"OK: {len(res.fns)} function(s), {ex} example(s) passed"
              + (f", {len(res.warns)} warning(s)" if res.warns else ''))


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
        for callee in f.calls:
            if callee in own:
                h = hits.setdefault(callee, [0, 0])
                h[0] += n_ex
                h[1] += 0 if bad else n_ex
    for name, (total, ok) in hits.items():
        own[name]['witness'] = f"{ok}/{total}"
        if own[name]['status'] != 'shell':
            own[name]['status'] = 'witnessed' if ok == total else 'witness-failed'
    return w


def cmd_trust(res, path, witness=None):
    ck = res.ck
    used = manifest(ck)
    print(f"TRUST LEDGER  {path}")
    print("  can:    " + ('; '.join(f"{EFFECT_DOC[u]} ({u})" for u in used) or "nothing but compute"))
    print("  cannot: " + '; '.join(EFFECT_DOC[u] for u in EFFECTS if u not in used)
          + "; network; run other programs")
    print()
    w = max(len(f['name']) for f in res.fns)
    print(f"  {'function'.ljust(w)}  status          examples  blocks  notes")
    for f in res.fns:
        st = f['status']
        exs = f"{f['passed']}/{f['examples']}" if st not in ('shell', 'assumed') or f['examples'] else '-'
        bl = f"{f['hit']}/{f['blocks']}" if f['blocks'] else '-'
        notes = []
        if st == 'assumed':
            notes.append(f"ASSUMED: \"{f['assume']}\"")
        if st == 'shell':
            notes.append("effects: " + (', '.join(f['uses']) or 'none'))
        if f['unreached'] and st == 'partial':
            notes.append("never ran: " + ', '.join(f"line {l} ({k})" for l, k in f['unreached']))
        if f.get('witness'):
            notes.append(f"independent examples passed: {f['witness']}")
        if f['rests_on']:
            notes.append("rests on assumed: " + ', '.join(f['rests_on']))
        print(f"  {f['name'].ljust(w)}  {st.ljust(14)}  {exs.ljust(8)}  {bl.ljust(6)}  {'; '.join(notes)}")
    cnt = {}
    for f in res.fns:
        cnt[f['status']] = cnt.get(f['status'], 0) + 1
    total = sum(f['lines'] for f in res.fns)
    shell = sum(f['lines'] for f in res.fns if f['status'] == 'shell')
    print()
    if witness is not None and witness.diags:
        print("  independent examples that failed:")
        for d in witness.diags:
            print(f"    {d.file}:{d.line}: {d.msg}")
        print()
    print("  self-checked = agrees with its author's own examples: catches slips, not a misread task.")
    print("  witnessed    = also passed examples written by someone else (--witness FILE).")
    print("  summary: " + ', '.join(f"{v} {k}" for k, v in cnt.items())
          + f". Unchecked shell = {shell} of {total} function lines ({100 * shell // max(total, 1)}%).")
    assumed = [f for f in res.fns if f['status'] == 'assumed']
    if assumed:
        print("  taken on faith:")
        for f in assumed:
            deps = [g['name'] for g in res.fns if f['name'] in g['rests_on']]
            print(f"    - {f['name']}: \"{f['assume']}\"" + (f"  -> affects: {', '.join(deps)}" if deps else ''))
    else:
        print("  taken on faith: nothing.")


def cmd_card(ck, path):
    print(f"// card of {path}: everything needed to call this code without reading its bodies")
    for it in ck.items:
        if it.k == 'Record':
            print(f"record {it.name} {{ " + ', '.join(f"{n}: {tystr(t)}" for n, t, _ in it.fields) + " }")
        elif it.k == 'Enum':
            vs = [v + (f"({', '.join(f'{n}: {tystr(t)}' for n, t, _ in fs)})" if fs else '') for v, fs, _ in it.variants]
            print(f"enum {it.name} {{ " + ', '.join(vs) + " }")
        elif it.k == 'Const':
            print(f"const {it.name}: {tystr(it.ty)}")
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


def build(res, out, opt='-O2'):
    if 'main' not in res.ck.fns:
        raise SystemExit("no `fn main()` in this program; nothing to run (use `dovod check` for libraries)")
    csrc = CGen(res.ck, 'run').program()
    compile_c(csrc, out, opt)
    try:
        os.remove(out + '.c')
    except OSError:
        pass


USAGE = """dovod — a language where code carries its evidence
  dovod check FILE [--json]     type-check, run every example, report
  dovod run   FILE [ARGS...]    check, compile, run
  dovod build FILE [-o OUT]     check, compile to a native binary
  dovod trust FILE [--witness W.dv] [--json]
                                ledger: what the program can do, what is checked, what is assumed;
                                W.dv = examples by another author (`use`s FILE)
  dovod card  FILE              interface only (signatures, contracts, examples) for another agent
  dovod spec                    print the whole language spec (fits in one context window)
"""


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ('-h', '--help', 'help'):
        print(USAGE)
        return 0
    cmd, a = a[0], a[1:]
    if cmd == 'spec':
        print(open(os.path.join(ROOT, 'SPEC.md'), encoding='utf-8').read())
        return 0
    if cmd not in ('check', 'run', 'build', 'trust', 'card') or not a:
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
        out = a[a.index('-o') + 1] if '-o' in a else os.path.splitext(os.path.basename(path))[0]
        build(res, os.path.abspath(out))
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
