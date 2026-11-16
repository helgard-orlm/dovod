"""Dovod: static checker. Types, names, effects, evidence rules. Annotates the AST for cgen."""
import difflib
from .syntax import Diag

ERR = '?'
SCALARS = ('Int', 'Float', 'Bool', 'Text')
EFFECTS = ('io', 'fs_read', 'fs_write', 'env', 'clock', 'rand')   # the core; modules add their own
EFFECT_DOC = {
    'io': 'read/write the console', 'fs_read': 'read files', 'fs_write': 'write files',
    'env': 'read command-line arguments', 'clock': 'read the clock', 'rand': 'use randomness',
}
OI, OF, OT, LT = ('Opt', 'Int'), ('Opt', 'Float'), ('Opt', 'Text'), ('List', 'Text')

# name: (param types, return type, effect)
BUILTINS = {
    'find': (['Text', 'Text'], OI, None),
    'contains': (['Text', 'Text'], 'Bool', None),
    'starts_with': (['Text', 'Text'], 'Bool', None),
    'ends_with': (['Text', 'Text'], 'Bool', None),
    'split': (['Text', 'Text'], LT, None),
    'join': ([LT, 'Text'], 'Text', None),
    'trim': (['Text'], 'Text', None),
    'replace': (['Text', 'Text', 'Text'], 'Text', None),
    'byte': (['Text', 'Int'], 'Int', None),
    'from_byte': (['Int'], 'Text', None),
    'parse_int': (['Text'], OI, None),
    'parse_float': (['Text'], OF, None),
    'float': (['Int'], 'Float', None),
    'trunc': (['Float'], 'Int', None),
    'sqrt': (['Float'], 'Float', None),
    'near': (['Float', 'Float', 'Float'], 'Bool', None),
    'trap': (['Text'], 'Never', None),
    'print': (['Text'], 'Void', 'io'),
    'read_line': ([], OT, 'io'),
    'read_file': (['Text'], OT, 'fs_read'),
    'write_file': (['Text', 'Text'], 'Bool', 'fs_write'),
    'args': ([], LT, 'env'),
    'now_ms': ([], 'Int', 'clock'),
    'rand_int': (['Int', 'Int'], 'Int', 'rand'),
}
POLY = ('len', 'show', 'slice', 'or_else', 'abs', 'min', 'max')


def tystr(t):
    if isinstance(t, tuple):
        return f"{t[0]}[{tystr(t[1])}]"
    return t


def same(a, b):
    return a == b or ERR in (a, b)


class Var:
    def __init__(self, ty, kind, node):
        self.ty, self.kind, self.node = ty, kind, node
        self.used = self.mutated = False


class FnInfo:
    def __init__(self, node):
        self.node, self.name = node, node.name
        self.params = [(n, t) for n, t, _ in node.params]
        self.ret = node.ret
        self.uses = [u for u, _ in node.uses]
        self.calls = set()
        self.used_effects = {}


def names_in(e, acc):
    """All variable names mentioned in an expression."""
    if e is None or not hasattr(e, 'k'):
        return acc
    if e.k == 'Name':
        acc.add(e.name)
    for v in e.__dict__.values():
        if hasattr(v, 'k'):
            names_in(v, acc)
        elif isinstance(v, list):
            for x in v:
                if hasattr(x, 'k'):
                    names_in(x, acc)
                elif isinstance(x, tuple):
                    for y in x:
                        if hasattr(y, 'k'):
                            names_in(y, acc)
    return acc


def ast_key(e):
    """Structure of an expression without positions, for spotting `A == A`."""
    if not hasattr(e, 'k'):
        if isinstance(e, (list, tuple)):
            return tuple(ast_key(x) for x in e)
        return e
    skip = ('file', 'line', 'col', 'ty', 'src', 'ref', 'builtin', 'effectful', 'variant')
    return (e.k,) + tuple((k, ast_key(v)) for k, v in sorted(e.__dict__.items()) if k not in skip)


def lv_root(e):
    while e.k in ('Field', 'Index'):
        e = e.e
    return e


def mutated_roots(stmts, acc):
    for s in stmts:
        if s.k == 'Assign':
            r = lv_root(s.target)
            if r.k == 'Name':
                acc.add(r.name)
        elif s.k == 'MethodStmt':
            r = lv_root(s.call.e)
            if r.k == 'Name':
                acc.add(r.name)
        elif s.k == 'If':
            mutated_roots(s.then, acc)
            if s.els:
                mutated_roots(s.els, acc)
        elif s.k in ('While', 'ForIn', 'ForRange'):
            mutated_roots(s.body, acc)
        elif s.k == 'Match':
            for _, body in s.arms:
                mutated_roots(body, acc)
    return acc


def calls_fn(e, name):
    if not hasattr(e, 'k'):
        return False
    if e.k == 'Call' and e.name == name:
        return True
    for v in e.__dict__.values():
        if hasattr(v, 'k') and calls_fn(v, name):
            return True
        if isinstance(v, list):
            for x in v:
                if hasattr(x, 'k') and calls_fn(x, name):
                    return True
                if isinstance(x, tuple) and any(hasattr(y, 'k') and calls_fn(y, name) for y in x):
                    return True
    return False


class Checker:
    def __init__(self, items, modules=None):
        self.items = items
        self.modules = modules or {}
        self.effects = list(EFFECTS)
        self.effect_doc = dict(EFFECT_DOC)
        self.effect_module = {}
        for mname, m in self.modules.items():
            for en, doc in m['effects'].items():
                if en not in self.effects:
                    self.effects.append(en)
                self.effect_doc[en] = doc
                self.effect_module[en] = mname
        self.errs = []
        self.records, self.enums, self.variants, self.consts, self.fns = {}, {}, {}, {}, {}
        self.type_nodes = {}
        self.scopes = []
        self.cur = None
        self.pure_ctx = None  # 'ex' | 'need' | 'give' when checking a clause
        self.loop_depth = 0
        self.late = []  # findings that must not stop the examples from running

    # -- diagnostics
    def err(self, code, node, msg, fix=None):
        self.errs.append(Diag(code, node.file, node.line, node.col, msg, fix))

    def nearest(self, name, pool):
        m = difflib.get_close_matches(name, list(pool), 1, 0.6)
        return m[0] if m else None

    # -- driver
    def owner_ok(self, it, name, is_type):
        """Names instead of namespaces: what a module exports starts with the module's name
        (`video_open`, `VideoKey`); nothing else may start that way."""
        def has(prefix_of):
            return name.startswith(prefix_of.capitalize()) if is_type else \
                (name == prefix_of or name.startswith(prefix_of + '_'))
        mod = getattr(it, 'module', None)
        if mod:
            if not has(mod):
                want = mod.capitalize() + name if is_type else mod + '_' + name
                self.err('E-MODULE', it, f"module `{mod}` must name this `{want}`: everything a module exports starts with its name",
                         "that is how a reader knows where a name comes from — Dovod has no namespaces")
        else:
            for m in self.modules:
                if has(m):
                    self.err('E-MODULE', it, f"the name `{name}` looks like it belongs to module `{m}`",
                             f"names starting with `{m.capitalize() if is_type else m + '_'}` are reserved for that module; pick another")

    def run(self):
        for it in self.items:
            if it.k == 'Effect' and it.name in EFFECTS:
                self.err('E-MODULE', it, f"`{it.name}` is a core effect; a module declares its own")
            if it.k in ('Record', 'Enum'):
                self.owner_ok(it, it.name, True)
                if it.k == 'Enum':
                    for v, _, vt in it.variants:
                        vt2 = type('N', (), {'file': vt.file, 'line': vt.line, 'col': vt.col, 'module': it.module})
                        self.owner_ok(vt2, v, True)
            elif it.k in ('Fn', 'Const') and it.name != 'main':
                self.owner_ok(it, it.name, False)
        for it in self.items:
            if it.k in ('Record', 'Enum'):
                if it.name in SCALARS or it.name in ('List', 'Opt', 'Void', 'Never') or it.name in self.type_nodes:
                    self.err('E-NAME', it, f"type name `{it.name}` is already taken")
                    continue
                self.type_nodes[it.name] = it
                if it.k == 'Record':
                    self.records[it.name] = [(n, t) for n, t, _ in it.fields]
                else:
                    self.enums[it.name] = [(v, [(n, t) for n, t, _ in fs]) for v, fs, _ in it.variants]
        for it in self.items:
            if it.k == 'Record':
                if not it.fields:
                    self.err('E-TYPE', it, f"record `{it.name}` has no fields")
                self.check_fields(it.fields)
            elif it.k == 'Enum':
                if not it.variants:
                    self.err('E-TYPE', it, f"enum `{it.name}` has no variants")
                for idx, (v, fs, vt) in enumerate(it.variants):
                    if v in self.variants or v in self.type_nodes:
                        self.err('E-NAME', vt, f"variant name `{v}` is already taken (variant names are global)")
                    else:
                        self.variants[v] = (it.name, idx, [(n, t) for n, t, _ in fs])
                    self.check_fields(fs)
        self.check_recursive_types()
        for it in self.items:
            if it.k == 'Const':
                self.check_const(it)
        for it in self.items:
            if it.k == 'Fn':
                if it.name in self.fns or it.name in BUILTINS or it.name in POLY or it.name in self.consts:
                    self.err('E-NAME', it, f"function name `{it.name}` is already taken")
                    continue
                for _, t, pn in it.params:
                    self.valid_type(t, pn)
                if it.ret != 'Void':
                    self.valid_type(it.ret, it)
                self.fns[it.name] = FnInfo(it)
        for f in list(self.fns.values()):
            self.check_fn(f)
        self.errs.sort(key=lambda d: (d.file, d.line, d.col))
        return self.errs

    def check_fields(self, fields):
        seen = set()
        for n, t, node in fields:
            if n in seen:
                self.err('E-NAME', node, f"duplicate field `{n}`")
            seen.add(n)
            self.valid_type(t, node)

    def valid_type(self, t, node):
        if isinstance(t, tuple):
            return self.valid_type(t[1], node)
        if t in SCALARS or t in self.type_nodes:
            return True
        near = self.nearest(t, list(SCALARS) + list(self.type_nodes))
        self.err('E-TYPE', node, f"unknown type `{t}`", f"did you mean `{near}`?" if near else None)
        return False

    def check_recursive_types(self):
        def deps(t):
            if isinstance(t, tuple):
                return deps(t[1]) if t[0] == 'Opt' else []
            return [t] if t in self.type_nodes else []

        graph = {}
        for n, fs in self.records.items():
            graph[n] = [d for _, t in fs for d in deps(t)]
        for n, vs in self.enums.items():
            graph[n] = [d for _, fs in vs for _, t in fs for d in deps(t)]
        state = {}

        def visit(n):
            if state.get(n) == 1:
                return True
            if state.get(n) == 2:
                return False
            state[n] = 1
            for d in graph.get(n, []):
                if visit(d):
                    return True
            state[n] = 2
            return False

        for n in graph:
            state.clear()
            if visit(n):
                self.err('E-TYPE', self.type_nodes[n], f"type `{n}` contains itself by value",
                         f"hold the nested `{n}` in a List (e.g. `kids: List[{n}]`)")
                return

    def check_const(self, it):
        e = it.e
        neg = False
        if e.k == 'Un' and e.op == '-':
            neg, e = True, e.e
        lit = {'Int': 'Int', 'Float': 'Float', 'Bool': 'Bool', 'Str': 'Text'}.get(e.k)
        if lit is None or (neg and lit not in ('Int', 'Float')):
            self.err('E-CONST', it, "const value must be a literal (Int, Float, Bool or Text)")
            return
        if lit != it.ty:
            self.err('E-TYPE', it, f"const `{it.name}` declared {tystr(it.ty)} but literal is {lit}")
            return
        if it.name in self.consts or it.name in BUILTINS or it.name in POLY:
            self.err('E-NAME', it, f"name `{it.name}` is already taken")
            return
        it.value = -e.v if neg else e.v
        self.consts[it.name] = it

    # -- scopes
    def lookup(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return None

    def declare(self, name, ty, kind, node):
        if name == 'out' and kind == 'param':
            self.err('E-NAME', node, "a parameter cannot be called `out`: that name is the result inside `give`")
        elif self.lookup(name) or name in self.fns or name in self.consts or name in BUILTINS or name in POLY:
            self.err('E-SHADOW', node, f"name `{name}` is already in use here; shadowing is not allowed",
                     "pick a different name")
        v = Var(ty, kind, node)
        self.scopes[-1][name] = v
        return v

    def push(self):
        self.scopes.append({})

    def pop(self):
        for name, v in self.scopes.pop().items():
            if name.startswith('_') or v.kind == 'out':
                continue
            if not v.used and not (v.kind == 'var' and v.mutated):
                what = 'parameter' if v.kind == 'param' else 'variable'
                self.err('E-UNUSED', v.node, f"{what} `{name}` is never used",
                         f"remove it or rename to `_{name}`")
            elif v.kind == 'var' and not v.mutated:
                self.err('E-VAR', v.node, f"`{name}` is declared `var` but never changed", "declare it with `let`")
            elif v.kind == 'var' and not v.used:
                self.err('E-UNUSED', v.node, f"variable `{name}` is written but never read")

    # -- functions
    def check_fn(self, f):
        n = f.node
        self.cur = f
        for u, un in n.uses:
            if u not in self.effects:
                self.err('E-EFFECT', un, f"unknown effect `{u}`", "effects: " + ', '.join(self.effects)
                         + " (a module's effect needs `use MODULE` at the top)")
        if len(set(f.uses)) != len(f.uses):
            self.err('E-EFFECT', n, "duplicate effect in `uses`")
        self.scopes = []
        self.push()
        seen = set()
        for pn, pt, node in n.params:
            if pn in seen:
                self.err('E-NAME', node, f"duplicate parameter `{pn}`")
            seen.add(pn)
            self.declare(pn, pt, 'param', node)
        for e in n.needs:
            self.clause(e, 'need')
        self.loop_depth = 0
        if n.extern:
            self.check_extern(f)
        else:
            self.block(n.body, new_scope=True)
        if not n.extern and f.ret != 'Void' and not self.returns(n.body):
            self.err('E-RETURN', n, f"function `{f.name}` can reach its end without returning {tystr(f.ret)}",
                     "add a `return` on every path (or `trap(\"...\")` where it cannot happen)")
        if n.gives:
            if f.ret == 'Void':
                self.err('E-CONTRACT', n.gives[0], "`give` needs a result; this function returns nothing")
            else:
                self.push()
                o = self.declare('out', f.ret, 'out', n)
                for e in n.gives:
                    self.clause(e, 'give')
                    if 'out' not in names_in(e, set()):
                        self.err('E-CONTRACT', e, "`give` must talk about the result `out`")
                o.used = True
                self.pop()
        self.pop()
        # effects: declared == used, exactly
        for eff, node in f.used_effects.items():
            if eff not in f.uses:
                have = ', '.join(f.uses + [eff])
                self.err('E-EFFECT', node, f"`{f.name}` does `{eff}` ({self.effect_doc[eff]}) but does not declare it",
                         f"add `uses {have}` to `fn {f.name}` — or move this out of a pure function")
        for u, un in n.uses:
            if u in self.effects and u not in f.used_effects and not n.extern:
                self.err('E-EFFECT', un, f"`{f.name}` declares `uses {u}` but never does it",
                         f"remove `{u}` from `uses`")
        # evidence
        pure = not f.uses and not f.used_effects
        if f.name == 'main':
            if n.params or f.ret != 'Void':
                self.err('E-MAIN', n, "`main` takes no parameters and returns nothing")
            if n.exs:
                self.err('E-EVIDENCE', n.exs[0], "`main` cannot have examples")
        elif pure:
            self.scopes = [{}]
            for e in n.exs:
                self.clause(e, 'ex')
                if e.k == 'Bin' and e.op in ('==', '!=', '<', '<=', '>', '>=') and ast_key(e.l) == ast_key(e.r):
                    # reported after the examples run, so it does not hide the other findings
                    self.late.append(Diag('E-VACUOUS', e.file, e.line, e.col,
                                          "both sides of this comparison are the same expression, so it pins down nothing",
                                          f"compare with a concrete expected value: `ex {f.name}(...) == <value>`"))
                elif not calls_fn(e, f.name):
                    self.err('E-EVIDENCE', e, f"this example does not call `{f.name}`, so it proves nothing about it",
                             f"write it as `ex {f.name}(...) == expected`")
            if not n.exs and not n.assume:
                self.err('E-EVIDENCE', n, f"pure function `{f.name}` has no evidence",
                         f"add at least one `ex {f.name}(...) == expected` line before the body "
                         f"(or `assume \"why it cannot be exampled\"`)")
        else:
            if n.exs:
                self.err('E-EVIDENCE', n.exs[0],
                         f"`{f.name}` has effects, so examples cannot run at check time",
                         "move the logic into a pure function with examples; keep this one a thin shell")
        self.cur = None

    def check_extern(self, f):
        """An extern fn: its body is native code. Only simple types cross the border."""
        n = f.node
        ok = ('Int', 'Float', 'Bool', 'Text', OI, OF, OT, LT)
        for pn, pt, node in n.params:
            if pt not in ok:
                self.err('E-MODULE', node, f"an extern parameter cannot be {tystr(pt)}",
                         "across the native border go only Int, Float, Bool, Text, Opt[Int|Float|Text], List[Text]; "
                         "build richer types in Dovod code on top (e.g. serialise to Text)")
            self.lookup(pn).used = True
        if f.ret != 'Void' and f.ret not in ok:
            self.err('E-MODULE', n, f"an extern function cannot return {tystr(f.ret)}",
                     "return Int, Float, Bool, Text, Opt[Int|Float|Text] or List[Text] and parse it in Dovod")
        if n.gives:
            self.err('E-MODULE', n.gives[0], "`give` on an extern would be checked against native code nobody examples",
                     "wrap the extern in a Dovod function and put the `give` there")
        if n.exs:
            self.err('E-EVIDENCE', n.exs[0], "an extern function cannot have examples: its code is not Dovod")
        if not n.uses and not n.assume:
            n.assume = ("native code (extern): not checked by Dovod", n)

    def clause(self, e, kind):
        self.pure_ctx = kind
        t = self.expr(e, 'Bool')
        self.pure_ctx = None
        if not same(t, 'Bool'):
            self.err('E-TYPE', e, f"`{kind}` must be a Bool expression, got {tystr(t)}")

    def returns(self, stmts):
        for s in stmts:
            if s.k == 'Return':
                return True
            if s.k == 'ExprStmt' and s.e.k == 'Call' and s.e.name == 'trap':
                return True
            if s.k == 'If' and s.els is not None and self.returns(s.then) and self.returns(s.els):
                return True
            if s.k == 'Match' and s.arms and all(self.returns(b) for _, b in s.arms):
                return True
            if s.k == 'While' and s.cond.k == 'Bool' and s.cond.v and not self.has_break(s.body):
                return True
        return False

    def has_break(self, stmts):
        for s in stmts:
            if s.k == 'Break':
                return True
            if s.k == 'If' and (self.has_break(s.then) or (s.els and self.has_break(s.els))):
                return True
            if s.k == 'Match' and any(self.has_break(b) for _, b in s.arms):
                return True
        return False

    # -- statements
    def block(self, stmts, new_scope=True):
        if new_scope:
            self.push()
        dead = False
        for s in stmts:
            if dead:
                self.err('E-UNREACHABLE', s, "this statement can never run")
                break
            self.stmt(s)
            dead = s.k in ('Return', 'Break', 'Continue')
        if new_scope:
            self.pop()

    def stmt(self, s):
        k = s.k
        f = self.cur
        if k == 'Let':
            if s.decl is not None:
                self.valid_type(s.decl, s)
            t = self.expr(s.e, s.decl)
            if s.decl is not None and not same(t, s.decl):
                self.mismatch(s.e, s.decl, t)
                t = s.decl
            if t in ('Void', 'Never'):
                self.err('E-TYPE', s, "this expression has no value")
                t = ERR
            s.ty = t
            self.declare(s.name, t, 'var' if s.mut else 'let', s)
        elif k == 'Assign':
            t = self.lvalue(s.target)
            et = self.expr(s.e, t if t != ERR else None)
            if not same(t, et):
                self.mismatch(s.e, t, et)
        elif k == 'MethodStmt':
            c = s.call
            t = self.lvalue(c.e)
            if c.name not in ('push', 'pop'):
                self.err('E-NAME', c, f"unknown method `.{c.name}`", "methods: `.push(x)` and `.pop()` on a `var` List")
            elif t != ERR and not (isinstance(t, tuple) and t[0] == 'List'):
                self.err('E-TYPE', c, f"`.{c.name}` needs a List, got {tystr(t)}")
            elif c.name == 'push':
                if len(c.args) != 1:
                    self.err('E-ARGS', c, "`.push` takes exactly one value")
                else:
                    elem = t[1] if t != ERR else None
                    at = self.expr(c.args[0], elem)
                    if elem and not same(at, elem):
                        self.mismatch(c.args[0], elem, at)
            elif c.args:
                self.err('E-ARGS', c, "`.pop()` takes no arguments")
        elif k == 'If':
            self.cond(s.cond, 'if')
            self.block(s.then)
            if s.els is not None:
                self.block(s.els)
        elif k == 'While':
            self.cond(s.cond, 'while')
            self.loop_depth += 1
            self.block(s.body)
            self.loop_depth -= 1
        elif k == 'ForRange':
            for e in (s.a, s.b):
                t = self.expr(e, 'Int')
                if not same(t, 'Int'):
                    self.mismatch(e, 'Int', t)
            self.push()
            self.declare(s.name, 'Int', 'loopvar', s)
            self.loop_depth += 1
            self.block(s.body)
            self.loop_depth -= 1
            self.pop()
        elif k == 'ForIn':
            t = self.expr(s.e)
            elem = ERR
            if isinstance(t, tuple) and t[0] == 'List':
                elem = t[1]
            elif t != ERR:
                self.err('E-TYPE', s.e, f"`for ... in` needs a List, got {tystr(t)}",
                         "for numbers use `for i in 0..n`")
            s.elem = elem
            s.snapshot = bool(names_in(s.e, set()) & mutated_roots(s.body, set()))
            self.push()
            self.declare(s.name, elem, 'loopvar', s)
            self.loop_depth += 1
            self.block(s.body)
            self.loop_depth -= 1
            self.pop()
        elif k == 'Match':
            self.match(s)
        elif k == 'Return':
            if f.ret == 'Void':
                if s.e is not None:
                    self.err('E-TYPE', s.e, f"`{f.name}` returns nothing")
            elif s.e is None:
                self.err('E-TYPE', s, f"`return` needs a {tystr(f.ret)} value")
            else:
                t = self.expr(s.e, f.ret)
                if not same(t, f.ret):
                    self.mismatch(s.e, f.ret, t)
        elif k in ('Break', 'Continue'):
            if self.loop_depth == 0:
                self.err('E-SYNTAX', s, f"`{k.lower()}` outside of a loop")
        elif k == 'ExprStmt':
            e = s.e
            if e.k != 'Call':
                self.expr(e)
                self.err('E-UNUSED', e, "this expression's value is not used")
                return
            t = self.expr(e, None, stmt=True)
            if t not in ('Void', 'Never', ERR):
                self.err('E-UNUSED', e, f"result of `{e.name}` ({tystr(t)}) is ignored",
                         f"bind it: `let _r = {e.name}(...)` or handle it")

    def cond(self, e, what):
        t = self.expr(e, 'Bool')
        if not same(t, 'Bool'):
            self.err('E-TYPE', e, f"`{what}` condition must be Bool, got {tystr(t)}",
                     "compare explicitly, e.g. `x != 0` or `len(xs) > 0`")

    def lvalue(self, e):
        """Type of an assignable place. Root must be a `var`."""
        if e.k == 'Name':
            v = self.lookup(e.name)
            if v is None:
                self.unknown_name(e)
                e.ty = ERR
                return ERR
            if v.kind != 'var':
                kind = {'let': 'declared with `let`', 'param': 'a parameter', 'loopvar': 'a loop variable',
                        'bind': 'a match binding', 'out': 'the result'}.get(v.kind, v.kind)
                self.err('E-IMMUTABLE', e, f"cannot change `{e.name}`: it is {kind}",
                         f"declare it with `var {e.name} = ...`" if v.kind == 'let'
                         else f"copy it first: `var my_{e.name} = {e.name}`")
            v.mutated = True
            e.ty = v.ty
            return v.ty
        if e.k == 'Field':
            bt = self.lvalue(e.e)
            e.ty = self.field_type(e, bt)
            return e.ty
        if e.k == 'Index':
            bt = self.lvalue(e.e)
            it = self.expr(e.i, 'Int')
            if not same(it, 'Int'):
                self.mismatch(e.i, 'Int', it)
            if bt == ERR:
                e.ty = ERR
            elif isinstance(bt, tuple) and bt[0] == 'List':
                e.ty = bt[1]
            else:
                self.err('E-TYPE', e, f"cannot index {tystr(bt)}")
                e.ty = ERR
            return e.ty
        self.err('E-SYNTAX', e, "this is not something that can be assigned to")
        return ERR

    def field_type(self, e, bt):
        if bt == ERR:
            return ERR
        if bt in self.records:
            for n, t in self.records[bt]:
                if n == e.name:
                    return t
            near = self.nearest(e.name, [n for n, _ in self.records[bt]])
            self.err('E-NAME', e, f"record `{bt}` has no field `{e.name}`",
                     f"did you mean `.{near}`?" if near else "fields: " + ', '.join(n for n, _ in self.records[bt]))
            return ERR
        self.err('E-TYPE', e, f"`.{e.name}`: {tystr(bt)} is not a record",
                 "use `match` to look inside an enum or Opt" if bt in self.enums or isinstance(bt, tuple) else None)
        return ERR

    def match(self, s):
        t = self.expr(s.e)
        s.mty = t
        s.snapshot = bool(names_in(s.e, set()) & mutated_roots([s], set()))
        if t == ERR:
            for _, body in s.arms:
                self.block(body)
            return
        is_opt = isinstance(t, tuple) and t[0] == 'Opt'
        if t in ('Int', 'Text', 'Bool'):
            return self.match_lit(s, t)
        if not is_opt and t not in self.enums:
            self.err('E-TYPE', s.e, f"`match` needs an enum, Opt, Int, Text or Bool, got {tystr(t)}")
            for _, body in s.arms:
                self.block(body)
            return
        for pat, _ in s.arms:
            if pat.k == 'PElse':
                self.err('E-MATCH', pat, f"`else` is not allowed when matching {tystr(t)}: name every case",
                         "that is the point of an enum — a new variant must not slip through silently")
        seen = []
        for pat, body in s.arms:
            self.push()
            if is_opt:
                if pat.k == 'PSome':
                    key = 'some'
                    self.declare(pat.name, t[1], 'bind', pat)
                elif pat.k == 'PNone':
                    key = 'none'
                else:
                    key = None
                    self.err('E-TYPE', pat, f"matching {tystr(t)}: arms are `some(name)` and `none`")
            elif pat.k == 'PElse':
                key = None
            else:
                key = None
                if pat.k != 'PVariant' or pat.name not in self.variants or self.variants[pat.name][0] != t:
                    names = ', '.join(v for v, _ in self.enums[t])
                    self.err('E-TYPE', pat, f"not a variant of `{t}`", f"variants: {names}")
                else:
                    key = pat.name
                    fields = dict(self.variants[pat.name][2])
                    pat.idx = self.variants[pat.name][1]
                    for b in pat.binds:
                        if b == '_':
                            continue
                        if b not in fields:
                            self.err('E-NAME', pat, f"variant `{pat.name}` has no field `{b}`",
                                     "bindings are field names: " + (', '.join(fields) or '(no fields)'))
                        else:
                            self.declare(b, fields[b], 'bind', pat)
            if key is not None:
                if key in seen:
                    self.err('E-MATCH', pat, f"duplicate arm `{key}`")
                seen.append(key)
            self.block(body)
            self.pop()
        want = ['some', 'none'] if is_opt else [v for v, _ in self.enums[t]]
        missing = [w for w in want if w not in seen]
        if missing:
            arms = ' '.join(f"{'some(x)' if m == 'some' else m} => {{ ... }}" for m in missing)
            self.err('E-MATCH', s, "match does not cover: " + ', '.join(missing), f"add arms: {arms}")

    def match_lit(self, s, t):
        """`match` on Int / Text / Bool: arms are literals (`1, 2 => {}`), then a mandatory `else`."""
        want_k = {'Int': 'Int', 'Text': 'Str', 'Bool': 'Bool'}[t]
        seen, has_else = set(), False
        for n, (pat, body) in enumerate(s.arms):
            if pat.k == 'PElse':
                has_else = True
                if n != len(s.arms) - 1:
                    self.err('E-MATCH', pat, "`else` must be the last arm")
            elif pat.k != 'PLit':
                self.err('E-TYPE', pat, f"matching {t}: arms are literals (`1, 2 => {{ ... }}`) and a final `else`")
            else:
                for lit in pat.lits:
                    lit.ty = t
                    if lit.k != want_k:
                        self.err('E-TYPE', lit, f"this pattern is not a {t} literal")
                    elif lit.v in seen:
                        self.err('E-MATCH', lit, f"duplicate pattern `{lit.v!r}`".replace("'", '"'))
                    seen.add(lit.v)
            self.block(body)
        if not has_else and not (t == 'Bool' and seen == {True, False}):
            self.err('E-MATCH', s, f"a match on {t} cannot list every value", "add a last arm: `else => { ... }`")

    # -- expressions
    def mismatch(self, e, want, got):
        fix = None
        if (want, got) == ('Float', 'Int'):
            fix = "no implicit conversion: wrap with `float(...)` or write the literal as `1.0`"
        elif (want, got) == ('Int', 'Float'):
            fix = "no implicit conversion: wrap with `trunc(...)`"
        elif isinstance(got, tuple) and got[0] == 'Opt':
            fix = "this may be `none`: use `match` or `or_else(value, default)`"
        elif want == 'Text' and got != 'Text':
            fix = "convert with `show(...)`"
        elif isinstance(want, tuple) and want[0] == 'Opt' and same(want[1], got):
            fix = "wrap it: `some(...)`"
        self.err('E-TYPE', e, f"expected {tystr(want)}, got {tystr(got)}", fix)

    def unknown_name(self, e):
        pool = [n for sc in self.scopes for n in sc] + list(self.consts)
        near = self.nearest(e.name, pool)
        self.err('E-NAME', e, f"unknown name `{e.name}`", f"did you mean `{near}`?" if near else None)

    def expr(self, e, exp=None, stmt=False):
        t = self.expr_(e, exp, stmt)
        e.ty = t
        return t

    def expr_(self, e, exp, stmt):
        k = e.k
        if k == 'Int':
            return 'Int'
        if k == 'Float':
            return 'Float'
        if k == 'Str':
            return 'Text'
        if k == 'Bool':
            return 'Bool'
        if k == 'Hole':
            scope = ', '.join(f"{n}: {tystr(v.ty)}" for sc in self.scopes for n, v in sc.items())
            for sc in self.scopes:
                for v in sc.values():
                    v.used = True
            want = tystr(exp) if exp else 'unknown (add a type annotation nearby)'
            self.err('E-HOLE', e, f"hole `?{e.name}` needs a value of type {want}; in scope: {scope or 'nothing'}")
            return exp or ERR
        if k == 'NoneLit':
            if isinstance(exp, tuple) and exp[0] == 'Opt':
                return exp
            self.err('E-TYPE', e, "cannot tell which Opt type this `none` is",
                     "annotate: `let x: Opt[Int] = none`")
            return ERR
        if k == 'Some':
            inner = exp[1] if isinstance(exp, tuple) and exp[0] == 'Opt' else None
            t = self.expr(e.e, inner)
            return ERR if t == ERR else ('Opt', t)
        if k == 'ListLit':
            elem = exp[1] if isinstance(exp, tuple) and exp[0] == 'List' else None
            if not e.items:
                if elem is None:
                    self.err('E-TYPE', e, "cannot tell the element type of `[]`",
                             "annotate: `var xs: List[Int] = []`")
                    return ERR
                return exp
            for it in e.items:
                t = self.expr(it, elem)
                if elem is None:
                    elem = t
                elif not same(t, elem):
                    self.mismatch(it, elem, t)
            return ERR if elem == ERR else ('List', elem)
        if k == 'Name':
            v = self.lookup(e.name)
            if v is not None:
                v.used = True
                e.ref = 'var'
                return v.ty
            if e.name in self.consts:
                e.ref = 'const'
                return self.consts[e.name].ty
            if e.name in self.fns or e.name in BUILTINS or e.name in POLY:
                self.err('E-NAME', e, f"`{e.name}` is a function; call it: `{e.name}(...)`")
                return ERR
            self.unknown_name(e)
            return ERR
        if k == 'Ctor':
            return self.ctor(e)
        if k == 'With':
            bt = self.expr(e.e, exp)
            if bt != ERR and bt not in self.records:
                self.err('E-TYPE', e, f"`with` changes fields of a record; this is {tystr(bt)}")
                bt = ERR
            if not e.fields:
                self.err('E-ARGS', e, "`with { }` changes nothing", "name at least one field: `p with { x: 1 }`")
            want = dict(self.records[bt]) if bt != ERR else {}
            got = set()
            for fname, fe in e.fields:
                if bt == ERR:
                    self.expr(fe)
                    continue
                if fname not in want:
                    near = self.nearest(fname, want)
                    self.err('E-NAME', fe, f"record `{bt}` has no field `{fname}`",
                             f"did you mean `{near}`?" if near else "fields: " + ', '.join(want))
                    self.expr(fe)
                    continue
                if fname in got:
                    self.err('E-NAME', fe, f"field `{fname}` given twice")
                got.add(fname)
                ft = self.expr(fe, want[fname])
                if not same(ft, want[fname]):
                    self.mismatch(fe, want[fname], ft)
            return bt
        if k == 'Un':
            if e.op == 'not':
                t = self.expr(e.e, 'Bool')
                if not same(t, 'Bool'):
                    self.mismatch(e.e, 'Bool', t)
                return 'Bool'
            t = self.expr(e.e, exp)
            if t not in ('Int', 'Float', ERR):
                self.err('E-TYPE', e, f"cannot negate {tystr(t)}")
                return ERR
            return t
        if k == 'Bin':
            return self.binop(e, exp)
        if k == 'Field':
            return self.field_type(e, self.expr(e.e))
        if k == 'Index':
            bt = self.expr(e.e)
            it = self.expr(e.i, 'Int')
            if not same(it, 'Int'):
                self.mismatch(e.i, 'Int', it)
            if bt == ERR:
                return ERR
            if isinstance(bt, tuple) and bt[0] == 'List':
                return bt[1]
            self.err('E-TYPE', e, f"cannot index {tystr(bt)}",
                     "for Text use `slice(t, i, j)` or `byte(t, i)`" if bt == 'Text' else None)
            return ERR
        if k == 'Method':
            self.err('E-SYNTAX', e, f"`.{e.name}(...)` is a statement, not a value",
                     "write it on its own line")
            return ERR
        if k == 'Call':
            return self.call(e, exp, stmt)
        raise AssertionError(k)

    def ctor(self, e):
        if e.name in self.records:
            fields, ty = self.records[e.name], e.name
            e.variant = None
        elif e.name in self.variants:
            ty, idx, fields = self.variants[e.name]
            e.variant = idx
        else:
            near = self.nearest(e.name, list(self.records) + list(self.variants))
            self.err('E-NAME', e, f"unknown record or variant `{e.name}`", f"did you mean `{near}`?" if near else None)
            for _, fe in e.fields:
                self.expr(fe)
            return ERR
        want = dict(fields)
        got = set()
        for fname, fe in e.fields:
            if fname not in want:
                near = self.nearest(fname, want)
                self.err('E-NAME', fe, f"`{e.name}` has no field `{fname}`",
                         f"did you mean `{near}`?" if near else None)
                self.expr(fe)
                continue
            if fname in got:
                self.err('E-NAME', fe, f"field `{fname}` given twice")
            got.add(fname)
            t = self.expr(fe, want[fname])
            if not same(t, want[fname]):
                self.mismatch(fe, want[fname], t)
        missing = [n for n, _ in fields if n not in got]
        if missing:
            self.err('E-ARGS', e, f"`{e.name}` is missing fields: " + ', '.join(missing),
                     f"`{e.name}(" + ', '.join(f"{n}: ..." for n, _ in fields) + ")`")
        return ty

    def binop(self, e, exp):
        op = e.op
        if op in ('and', 'or'):
            for side in (e.l, e.r):
                t = self.expr(side, 'Bool')
                if not same(t, 'Bool'):
                    self.mismatch(side, 'Bool', t)
            return 'Bool'
        cmp = op in ('==', '!=', '<', '<=', '>', '>=')
        lt = self.expr(e.l, None if cmp else exp)
        rt = self.expr(e.r, lt if lt != ERR else None)
        if lt == ERR or rt == ERR:
            return 'Bool' if cmp else ERR
        if lt != rt:
            self.mismatch(e.r, lt, rt)
            return 'Bool' if cmp else ERR
        if op in ('==', '!='):
            return 'Bool'
        if cmp:
            if lt not in ('Int', 'Float', 'Text'):
                self.err('E-TYPE', e, f"cannot order {tystr(lt)} with `{op}`")
            return 'Bool'
        if op == '+':
            if lt in ('Int', 'Float', 'Text') or (isinstance(lt, tuple) and lt[0] == 'List'):
                return lt
        elif op == '%':
            if lt == 'Int':
                return lt
        elif lt in ('Int', 'Float'):
            return lt
        self.err('E-TYPE', e, f"operator `{op}` does not work on {tystr(lt)}")
        return ERR

    def call(self, e, exp, stmt):
        name = e.name
        f = self.cur
        if name in POLY:
            if any(e.argnames):
                self.err('E-ARGS', e, f"built-in `{name}` takes its arguments by position, without names")
            return self.poly(e, exp)
        if name in BUILTINS:
            params, ret, eff = BUILTINS[name]
            e.builtin, e.effectful = True, eff is not None
            self.args(e, params)
            if any(e.argnames):
                self.err('E-ARGS', e, f"built-in `{name}` takes its arguments by position, without names")
            if eff:
                self.effect(e, eff)
            if ret == 'Never' and not stmt:
                self.err('E-SYNTAX', e, "`trap(...)` is a statement; write it on its own line")
                return ERR
            return ret
        if name in self.fns:
            g = self.fns[name]
            e.builtin, e.effectful = False, bool(g.uses)
            self.args(e, [t for _, t in g.params])
            if len(e.args) == len(g.params):
                for a, an, (pn, _) in zip(e.args, e.argnames, g.params):
                    if an is not None and an != pn:
                        self.err('E-ARGS', a, f"this argument is labelled `{an}` but this position of `{name}` is `{pn}`",
                                 f"order: {name}(" + ', '.join(n for n, _ in g.params) + ") — labels check the order, they do not change it")
            for eff in g.uses:
                if eff in self.effects:
                    self.effect(e, eff)
            if not self.pure_ctx:
                f.calls.add(name)
            if g.ret == 'Void' and not stmt:
                self.err('E-TYPE', e, f"`{name}` returns nothing; it cannot be used as a value")
                return ERR
            return g.ret
        near = self.nearest(name, list(self.fns) + list(BUILTINS) + list(POLY))
        fix = f"did you mean `{near}`?" if near else None
        if name in ('str', 'string', 'to_string', 'repr'):
            fix = "use `show(x)`"
        elif name in ('int',):
            fix = "use `trunc(f)` for Float or `parse_int(t)` for Text"
        elif name in ('append',):
            fix = "use the statement `xs.push(x)`"
        self.err('E-NAME', e, f"unknown function `{name}`", fix)
        for a in e.args:
            self.expr(a)
        return ERR

    def effect(self, e, eff):
        if self.pure_ctx:
            self.err('E-EFFECT', e, f"`{self.pure_ctx}` must be pure but `{e.name}` does `{eff}`")
        else:
            self.cur.used_effects.setdefault(eff, e)

    def args(self, e, params):
        if len(e.args) != len(params):
            self.err('E-ARGS', e, f"`{e.name}` takes {len(params)} argument(s), got {len(e.args)}",
                     f"signature: {e.name}(" + ', '.join(tystr(p) for p in params) + ")")
            for a in e.args:
                self.expr(a)
            return
        for a, p in zip(e.args, params):
            t = self.expr(a, p)
            if not same(t, p):
                self.mismatch(a, p, t)

    def poly(self, e, exp):
        name, a = e.name, e.args
        e.builtin, e.effectful = True, False
        arity = {'len': 1, 'show': 1, 'slice': 3, 'or_else': 2, 'abs': 1, 'min': 2, 'max': 2}[name]
        if len(a) != arity:
            self.err('E-ARGS', e, f"`{name}` takes {arity} argument(s), got {len(a)}")
            for x in a:
                self.expr(x)
            return ERR
        if name == 'or_else':
            ot = self.expr(a[0], ('Opt', exp) if exp else None)
            if ot == ERR:
                self.expr(a[1])
                return ERR
            if not (isinstance(ot, tuple) and ot[0] == 'Opt'):
                self.err('E-TYPE', a[0], f"`or_else` needs an Opt, got {tystr(ot)}")
                self.expr(a[1])
                return ERR
            dt = self.expr(a[1], ot[1])
            if not same(dt, ot[1]):
                self.mismatch(a[1], ot[1], dt)
            return ot[1]
        t = self.expr(a[0], exp if name in ('abs', 'min', 'max') else None)
        if name == 'show':
            if t in ('Void', 'Never'):
                self.err('E-TYPE', a[0], "nothing to show")
            return 'Text'
        if name == 'len':
            if t != ERR and t != 'Text' and not (isinstance(t, tuple) and t[0] == 'List'):
                self.err('E-TYPE', a[0], f"`len` needs Text or List, got {tystr(t)}")
            return 'Int'
        if name == 'slice':
            for x in a[1:]:
                xt = self.expr(x, 'Int')
                if not same(xt, 'Int'):
                    self.mismatch(x, 'Int', xt)
            if t != ERR and t != 'Text' and not (isinstance(t, tuple) and t[0] == 'List'):
                self.err('E-TYPE', a[0], f"`slice` needs Text or List, got {tystr(t)}")
                return ERR
            return t
        # abs, min, max
        if t not in ('Int', 'Float', ERR):
            self.err('E-TYPE', a[0], f"`{name}` needs Int or Float, got {tystr(t)}")
            t = ERR
        for x in a[1:]:
            xt = self.expr(x, t if t != ERR else None)
            if not same(xt, t):
                self.mismatch(x, t, xt)
        return t
