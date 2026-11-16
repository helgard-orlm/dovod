"""Dovod: C code generator. Value semantics: every heap value has exactly one owner.

Expression results are either *owned* (fresh; must be moved into a place or parked in a scope temp)
or *borrowed* (a view of a variable/field; copied when stored). No references exist in the language,
so a borrow can only be invalidated inside the statement that made it; loops and matches that mutate
what they look at take a snapshot (decided by the checker).
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SC = {'Int': 'int64_t', 'Float': 'double', 'Bool': 'bool', 'Text': 'dv_text', 'Void': 'void', 'Never': 'void'}
PF = {'Int': 'dv_int', 'Float': 'dv_float', 'Bool': 'dv_bool', 'Text': 'dv_text'}
ALWAYS = [('Opt', 'Int'), ('Opt', 'Float'), ('Opt', 'Text'), ('List', 'Text')]

SIMPLE = {
    'find': 'dv_find({0}, {1})', 'contains': '(dv_find_raw({0}, {1}) >= 0)',
    'starts_with': 'dv_starts({0}, {1})', 'ends_with': 'dv_ends({0}, {1})',
    'split': 'dv_split({0}, {1}, {L})', 'join': 'dv_join({0}, {1})', 'trim': 'dv_trim({0})',
    'replace': 'dv_replace({0}, {1}, {2}, {L})', 'byte': 'dv_byte({0}, {1}, {L})',
    'from_byte': 'dv_from_byte({0}, {L})', 'parse_int': 'dv_parse_int({0})',
    'parse_float': 'dv_parse_float({0})', 'float': '((double)({0}))', 'trunc': 'dv_trunc({0}, {L})',
    'sqrt': 'sqrt({0})', 'near': '(fabs(({0}) - ({1})) <= ({2}))', 'trap': 'dv_trap_text({0}, {L})',
    'print': 'dv_print({0})', 'read_line': 'dv_read_line()', 'read_file': 'dv_read_file({0})',
    'write_file': 'dv_write_file({0}, {1})', 'args': 'dv_args()', 'now_ms': 'dv_now_ms()',
    'rand_int': 'dv_rand_int({0}, {1}, {L})',
    'video_open': 'dv_video_open({0})', 'video_pick': 'dv_video_pick()', 'video_event': 'dv_video_event()',
    'video_pause': 'dv_video_pause({0})', 'video_seek': 'dv_video_seek({0})', 'video_pos': 'dv_video_pos()',
    'video_len': 'dv_video_len()', 'video_volume': 'dv_video_volume({0}, {L})', 'video_mute': 'dv_video_mute({0})',
    'video_rate': 'dv_video_rate({0}, {L})', 'video_fullscreen': 'dv_video_fullscreen({0})',
    'video_status': 'dv_video_status({0})', 'video_title': 'dv_video_title({0})',
}


def cstr(s):
    out = []
    for b in s.encode('utf-8'):
        if 32 <= b < 127 and b not in (34, 92, 63):
            out.append(chr(b))
        else:
            out.append('\\%03o' % b)
    return '"' + ''.join(out) + '"'


def mg(t):
    if isinstance(t, tuple):
        return ('L_' if t[0] == 'List' else 'O_') + mg(t[1])
    return t


def cty(t):
    if isinstance(t, tuple):
        return mg(t)
    return SC.get(t) or 'T_' + t


def pf(t):
    return PF.get(t) or cty(t)


class Scope:
    def __init__(self, kind):
        self.kind, self.owned = kind, []


class CGen:
    def __init__(self, ck, mode):
        self.ck, self.mode = ck, mode
        self.types = []          # composite (tuple) types in registration order
        self.heap_cache = {}
        self.n = 0
        self.cov = []            # id -> (fn, line, kind, counts)
        self.exs = []            # (fn name, expr node)
        self.buf, self.declbuf, self.scopes = [], [], []
        self.fn = None
        self.mut_on = False      # generating the body of a function whose examples can judge mutants
        self.bmuts = []          # body mutants: {'fn', 'line', 'col', 'what'}; id = index + 1
        for t in ALWAYS:
            self.reg(t)
        for fs in ck.records.values():
            for _, t in fs:
                self.reg(t)
        for vs in ck.enums.values():
            for _, fs in vs:
                for _, t in fs:
                    self.reg(t)

    # -- types
    def reg(self, t):
        if isinstance(t, tuple):
            self.reg(t[1])
            if t not in self.types:
                self.types.append(t)
        return t

    def heap(self, t):
        if t in self.heap_cache:
            return self.heap_cache[t]
        if t == 'Text' or (isinstance(t, tuple) and t[0] == 'List'):
            r = True
        elif isinstance(t, tuple):
            r = self.heap(t[1])
        elif t in self.ck.records:
            self.heap_cache[t] = False
            r = any(self.heap(ft) for _, ft in self.ck.records[t])
        elif t in self.ck.enums:
            self.heap_cache[t] = False
            r = any(self.heap(ft) for _, fs in self.ck.enums[t] for _, ft in fs)
        else:
            r = False
        self.heap_cache[t] = r
        return r

    def cty(self, t):
        self.reg(t)
        return cty(t)

    def copy(self, t, x):
        return f"{pf(self.reg(t))}_copy({x})" if self.heap(t) else x

    def free(self, t, x):
        return f"{pf(self.reg(t))}_free({x});" if self.heap(t) else ''

    def eq(self, t, a, b):
        if t in ('Int', 'Float', 'Bool'):
            return f"({a} == {b})"
        return f"{pf(self.reg(t))}_eq({a}, {b})"

    def show(self, t, b, x):
        return f"{pf(self.reg(t))}_show({b}, {x});"

    def gen_types(self):
        ck = self.ck
        fw, lists, structs, protos, bodies = [], [], [], [], []
        named = list(ck.records) + list(ck.enums)
        i = 0
        while i < len(self.types):  # registration may grow while iterating
            i += 1
        for t in named + self.types:
            fw.append(f"typedef struct {cty(t)} {cty(t)};")
        for t in self.types:
            if t[0] == 'List':
                lists.append(f"struct {cty(t)} {{ {cty(t[1])}* p; int64_t n, cap; }};")
        done = set()

        def deps(t):
            if isinstance(t, tuple):
                return [t[1]] if (t[0] == 'Opt' and (isinstance(t[1], tuple) or t[1] not in SC)) else []
            fts = [ft for _, ft in ck.records[t]] if t in ck.records else \
                [ft for _, fs in ck.enums[t] for _, ft in fs]
            return [ft for ft in fts if (isinstance(ft, tuple) and ft[0] == 'Opt') or
                    (not isinstance(ft, tuple) and ft not in SC)]

        def emit_struct(t):
            if t in done or (isinstance(t, tuple) and t[0] == 'List'):
                return
            done.add(t)
            for d in deps(t):
                emit_struct(d)
            X = cty(t)
            if isinstance(t, tuple):
                structs.append(f"struct {X} {{ bool has; {cty(t[1])} v; }};")
            elif t in ck.records:
                fs = ' '.join(f"{cty(ft)} f_{fn};" for fn, ft in ck.records[t])
                structs.append(f"struct {X} {{ {fs} }};")
            else:
                parts = []
                for v, fs in ck.enums[t]:
                    if fs:
                        parts.append("struct { " + ' '.join(f"{cty(ft)} f_{fn};" for fn, ft in fs) + f" }} V_{v};")
                u = f" union {{ {' '.join(parts)} }} u;" if parts else ''
                structs.append(f"struct {X} {{ int tag;{u} }};")

        for t in named + self.types:
            emit_struct(t)

        def fn(proto, body):
            protos.append(f"static {proto};")
            bodies.append(f"static {proto} {{ {body} }}")

        for t in named + self.types:
            X, h = cty(t), self.heap(t)
            if isinstance(t, tuple) and t[0] == 'List':
                et = t[1]
                E = cty(et)
                fe = self.free(et, 'a.p[i]')
                fn(f"{X} {X}_copy({X} a)",
                   f"{X} r; r.n = a.n; r.cap = a.n; r.p = a.n ? dv_alloc(sizeof({E}) * (size_t)a.n) : NULL; "
                   f"for (int64_t i = 0; i < a.n; i++) r.p[i] = {self.copy(et, 'a.p[i]')}; return r;")
                fn(f"void {X}_free({X} a)", (f"for (int64_t i = 0; i < a.n; i++) {{ {fe} }} " if fe else '') + "free(a.p);")
                fn(f"bool {X}_eq({X} a, {X} b)",
                   f"if (a.n != b.n) return false; for (int64_t i = 0; i < a.n; i++) "
                   f"if (!{self.eq(et, 'a.p[i]', 'b.p[i]')}) return false; return true;")
                fn(f"void {X}_push({X}* a, {E} v)",
                   f"if (a->n == a->cap) {{ a->cap = a->cap ? a->cap * 2 : 4; "
                   f"a->p = realloc(a->p, sizeof({E}) * (size_t)a->cap); if (!a->p) dv_trap(\"out of memory\", \"runtime\"); }} "
                   f"a->p[a->n++] = v;")
                fn(f"void {X}_pop({X}* a, const char* loc)",
                   f"if (!a->n) dv_trap(\"pop from an empty list\", loc); a->n--; {self.free(et, 'a->p[a->n]')}")
                fn(f"{E}* {X}_at({X}* a, int64_t i, const char* loc)",
                   "if (i < 0 || i >= a->n) dv_trap_idx(i, a->n, loc); return &a->p[i];")
                fn(f"{E} {X}_get({X} a, int64_t i, const char* loc)",
                   "if (i < 0 || i >= a.n) dv_trap_idx(i, a.n, loc); return a.p[i];")
                fn(f"{X} {X}_concat({X} a, {X} b)",
                   f"{X} r; r.n = a.n + b.n; r.cap = r.n; r.p = r.n ? dv_alloc(sizeof({E}) * (size_t)r.n) : NULL; "
                   f"for (int64_t i = 0; i < a.n; i++) r.p[i] = {self.copy(et, 'a.p[i]')}; "
                   f"for (int64_t i = 0; i < b.n; i++) r.p[a.n + i] = {self.copy(et, 'b.p[i]')}; return r;")
                fn(f"{X} {X}_slice({X} a, int64_t i, int64_t j, const char* loc)",
                   f"if (i < 0 || j < i || j > a.n) dv_trap(\"slice bounds out of range\", loc); "
                   f"{X} r; r.n = j - i; r.cap = r.n; r.p = r.n ? dv_alloc(sizeof({E}) * (size_t)r.n) : NULL; "
                   f"for (int64_t k = 0; k < r.n; k++) r.p[k] = {self.copy(et, 'a.p[i + k]')}; return r;")
                fn(f"{X} {X}_perturb({X} a)", f"{X} r = {{0}}; for (int64_t i = 0; i < a.n; i++) {X}_push(&r, {pf(self.reg(et))}_perturb(a.p[i])); "
                                               f"{E} z = {{0}}; {X}_push(&r, z); return r;")
                fn(f"void {X}_show(dv_buf* b, {X} a)",
                   f"dv_buf_puts(b, \"[\"); for (int64_t i = 0; i < a.n; i++) {{ if (i) dv_buf_puts(b, \", \"); "
                   f"{self.show(et, 'b', 'a.p[i]')} }} dv_buf_puts(b, \"]\");")
            elif isinstance(t, tuple):
                et = t[1]
                if h:
                    fn(f"{X} {X}_copy({X} a)", f"{X} r = a; if (a.has) r.v = {self.copy(et, 'a.v')}; return r;")
                    fn(f"void {X}_free({X} a)", f"if (a.has) {{ {self.free(et, 'a.v')} }}")
                fn(f"bool {X}_eq({X} a, {X} b)",
                   f"if (a.has != b.has) return false; return !a.has || {self.eq(et, 'a.v', 'b.v')};")
                fn(f"{X} {X}_perturb({X} a)", f"{X} r = {{0}}; r.has = !a.has; return r;")
                fn(f"void {X}_show(dv_buf* b, {X} a)",
                   f"if (!a.has) {{ dv_buf_puts(b, \"none\"); return; }} dv_buf_puts(b, \"some(\"); "
                   f"{self.show(et, 'b', 'a.v')} dv_buf_puts(b, \")\");")
            elif t in ck.records:
                fs = ck.records[t]
                if h:
                    fn(f"{X} {X}_copy({X} a)", f"{X} r = a; " + ' '.join(
                        f"r.f_{n} = {self.copy(ft, 'a.f_' + n)};" for n, ft in fs if self.heap(ft)) + " return r;")
                    fn(f"void {X}_free({X} a)", ' '.join(self.free(ft, 'a.f_' + n) for n, ft in fs))
                fn(f"bool {X}_eq({X} a, {X} b)",
                   "return " + ' && '.join(self.eq(ft, 'a.f_' + n, 'b.f_' + n) for n, ft in fs) + ";")
                fn(f"{X} {X}_perturb({X} a)", f"{X} r; " + ' '.join(
                    f"r.f_{n} = {pf(self.reg(ft))}_perturb(a.f_{n});" for n, ft in fs) + " return r;")
                sh = [f"dv_buf_puts(b, {cstr(t + '(')});"]
                for k, (n, ft) in enumerate(fs):
                    sh.append(f"dv_buf_puts(b, {cstr((', ' if k else '') + n + ': ')}); {self.show(ft, 'b', 'a.f_' + n)}")
                sh.append('dv_buf_puts(b, ")");')
                fn(f"void {X}_show(dv_buf* b, {X} a)", ' '.join(sh))
            else:
                vs = ck.enums[t]
                if h:
                    cp, fr = [], []
                    for k, (v, fs) in enumerate(vs):
                        hf = [(n, ft) for n, ft in fs if self.heap(ft)]
                        if hf:
                            cp.append(f"case {k}: " + ' '.join(
                                f"r.u.V_{v}.f_{n} = {self.copy(ft, f'a.u.V_{v}.f_{n}')};" for n, ft in hf) + " break;")
                            fr.append(f"case {k}: " + ' '.join(
                                self.free(ft, f'a.u.V_{v}.f_{n}') for n, ft in hf) + " break;")
                    fn(f"{X} {X}_copy({X} a)", f"{X} r = a; switch (a.tag) {{ {' '.join(cp)} default: break; }} return r;")
                    fn(f"void {X}_free({X} a)", f"switch (a.tag) {{ {' '.join(fr)} default: break; }}")
                eqs, shs = [], []
                for k, (v, fs) in enumerate(vs):
                    cond = ' && '.join(self.eq(ft, f'a.u.V_{v}.f_{n}', f'b.u.V_{v}.f_{n}') for n, ft in fs) or 'true'
                    eqs.append(f"case {k}: return {cond};")
                    sh = [f"dv_buf_puts(b, {cstr(v + ('(' if fs else ''))});"]
                    for j, (n, ft) in enumerate(fs):
                        sh.append(f"dv_buf_puts(b, {cstr((', ' if j else '') + n + ': ')}); "
                                  f"{self.show(ft, 'b', f'a.u.V_{v}.f_{n}')}")
                    if fs:
                        sh.append('dv_buf_puts(b, ")");')
                    shs.append(f"case {k}: {' '.join(sh)} break;")
                fn(f"bool {X}_eq({X} a, {X} b)",
                   f"if (a.tag != b.tag) return false; switch (a.tag) {{ {' '.join(eqs)} default: return true; }}")
                pts = []
                for k2, (v, fs) in enumerate(vs):
                    if fs:
                        pts.append(f"case {k2}: r.tag = {k2}; " + ' '.join(
                            f"r.u.V_{v}.f_{n} = {pf(self.reg(ft))}_perturb(a.u.V_{v}.f_{n});" for n, ft in fs) + " break;")
                    else:
                        pts.append(f"case {k2}: r.tag = {(k2 + 1) % len(vs)}; break;")
                fn(f"{X} {X}_perturb({X} a)", f"{X} r = {{0}}; switch (a.tag) {{ {' '.join(pts)} default: break; }} return r;")
                fn(f"void {X}_show(dv_buf* b, {X} a)", f"switch (a.tag) {{ {' '.join(shs)} default: break; }}")
        return fw + lists + structs + protos + bodies

    # -- emission plumbing
    def emit(self, s):
        if s:
            self.buf.append(s)

    def decl(self, s):
        self.declbuf.append(s)

    def name(self, p):
        self.n += 1
        return f"{p}{self.n}"

    def newtemp(self, t):
        """Zero-initialised temp owned by the current scope (freed at scope exit)."""
        x = self.name('t')
        self.decl(f"{self.cty(t)} {x} = {{0}};")
        self.scopes[-1].owned.append((x, t))
        return x

    def plain(self, t, init=None):
        """Unowned scratch variable (holds a borrow, a scalar, or a value about to be moved)."""
        x = self.name('s')
        self.decl(f"{self.cty(t)} {x} = {{0}};")
        if init is not None:
            self.emit(f"{x} = {init};")
        return x

    def loc(self, node):
        return cstr(f"{os.path.basename(node.file)}:{node.line}")

    def newcov(self, line, kind, stmts=None):
        trap_only = bool(stmts) and stmts[0].k == 'ExprStmt' and stmts[0].e.k == 'Call' and stmts[0].e.name == 'trap'
        self.cov.append({'fn': self.fn.name if self.fn else '?', 'line': line, 'kind': kind, 'skip': trap_only})
        return len(self.cov) - 1

    def frees(self, scopes, skip=None):
        for sc in reversed(scopes):
            for x, t in reversed(sc.owned):
                if x != skip:
                    self.emit(self.free(t, x))

    def newmut(self, node, what):
        self.bmuts.append({'fn': self.fn.name, 'file': node.file, 'line': node.line, 'col': node.col, 'what': what})
        return len(self.bmuts)

    def alt(self, node, what, normal, mutant):
        """`normal`, or — when this body mutant is switched on — `mutant`."""
        if not self.mut_on:
            return normal
        return f"(dv_bm == {self.newmut(node, what)} ? {mutant} : {normal})"

    # -- expressions
    def val(self, e, sink=False):
        c, owned = self.g(e)
        t = e.ty
        if not self.heap(t):
            return c
        if sink:
            return c if owned else self.copy(t, c)
        if owned:
            tmp = self.newtemp(t)
            self.emit(f"{tmp} = {c};")
            return tmp
        return c

    def g(self, e):
        k, t = e.k, e.ty
        if k == 'Int':
            if e.v < 0:
                return f"(-INT64_C({-e.v}))", False
            c = f"INT64_C({e.v})"
            if e.v < 2 ** 62:
                c = self.alt(e, f"`{e.v}` becomes `{e.v + 1}`", c, f"INT64_C({e.v + 1})")
            return c, False
        if k == 'Float':
            if e.v + 1.0 == e.v:   # too large for +1.0 to change it: no mutant
                return repr(e.v), False
            return self.alt(e, f"`{e.v!r}` becomes `{e.v + 1.0!r}`", repr(e.v), repr(e.v + 1.0)), False
        if k == 'Bool':
            a, b = ('true', 'false') if e.v else ('false', 'true')
            return self.alt(e, f"`{a}` becomes `{b}`", a, b), False
        if k == 'Str':
            return f"DV_LIT({cstr(e.v)}, {len(e.v.encode('utf-8'))})", False
        if k == 'Name':
            return ('v_' if e.ref == 'var' else 'C_') + e.name, False
        if k == 'NoneLit':
            return f"(({self.cty(t)}){{0}})", True
        if k == 'Some':
            return f"(({self.cty(t)}){{true, {self.val(e.e, sink=True)}}})", True
        if k == 'ListLit':
            if not e.items:
                return f"(({self.cty(t)}){{0}})", True
            x = self.plain(t)
            for it in e.items:
                self.emit(f"{cty(t)}_push(&{x}, {self.val(it, sink=True)});")
            return x, True
        if k == 'Ctor':
            fs = ', '.join(f".f_{n} = {self.val(fe, sink=True)}" for n, fe in e.fields)
            if e.variant is None:
                return f"(({self.cty(t)}){{{fs}}})", True
            if not e.fields:
                return f"(({self.cty(t)}){{.tag = {e.variant}}})", True
            return f"(({self.cty(t)}){{.tag = {e.variant}, .u.V_{e.name} = {{{fs}}}}})", True
        if k == 'With':
            x = self.plain(t, self.val(e.e, sink=True))
            fts = dict(self.ck.records[t])
            vals = [(n, self.plain(fts[n], self.val(fe, sink=True))) for n, fe in e.fields]
            for n, v in vals:
                self.emit(self.free(fts[n], f"{x}.f_{n}"))
                self.emit(f"{x}.f_{n} = {v};")
            return x, True
        if k == 'Un':
            c = self.val(e.e)
            if e.op == 'not':
                return self.alt(e, "`not` is dropped", f"(!{c})", f"({c})"), False
            return (f"dv_neg({c}, {self.loc(e)})" if t == 'Int' else f"(-({c}))"), False
        if k == 'Bin':
            return self.g_bin(e)
        if k == 'Field':
            return f"{self.val(e.e)}.f_{e.name}", False
        if k == 'Index':
            return f"{self.cty(e.e.ty)}_get({self.val(e.e)}, {self.val(e.i)}, {self.loc(e)})", False
        if k == 'Call':
            return self.g_call(e)
        raise AssertionError(k)

    def g_bin(self, e):
        op, lt = e.op, e.l.ty
        if op in ('and', 'or'):
            l = self.val(e.l)
            saved = self.buf
            self.buf = []
            r = self.val(e.r)
            rb, self.buf = self.buf, saved
            if not rb:
                a, b = ('&&', '||') if op == 'and' else ('||', '&&')
                other = 'or' if op == 'and' else 'and'
                return self.alt(e, f"`{op}` becomes `{other}`", f"({l} {a} {r})", f"({l} {b} {r})"), False
            x = self.plain('Bool', l)
            cond = self.alt(e, f"`{op}` becomes `{'or' if op == 'and' else 'and'}`",
                            x if op == 'and' else '!' + x, '!' + x if op == 'and' else x)
            self.emit(f"if ({cond}) {{")
            self.buf.extend(rb)
            self.emit(f"{x} = {r};")
            self.emit("}")
            return x, False
        a, b, L = self.val(e.l), self.val(e.r), self.loc(e)
        if op in ('==', '!='):
            c = self.eq(lt, a, b)
            n = f"(!{c})"
            if op == '==':
                return self.alt(e, "`==` becomes `!=`", c, n), False
            return self.alt(e, "`!=` becomes `==`", n, c), False
        if op in ('<', '<=', '>', '>='):
            o2 = {'<': '<=', '<=': '<', '>': '>=', '>=': '>'}[op]
            if lt == 'Text':
                return self.alt(e, f"`{op}` becomes `{o2}`", f"(dv_text_cmp({a}, {b}) {op} 0)",
                                f"(dv_text_cmp({a}, {b}) {o2} 0)"), False
            return self.alt(e, f"`{op}` becomes `{o2}`", f"({a} {op} {b})", f"({a} {o2} {b})"), False
        fl = {'+': '-', '-': '+'}.get(op)
        if lt == 'Int':
            fs = {'+': 'dv_add', '-': 'dv_sub', '*': 'dv_mul', '/': 'dv_div', '%': 'dv_mod'}
            c = f"{fs[op]}({a}, {b}, {L})"
            if fl:
                c = self.alt(e, f"`{op}` becomes `{fl}`", c, f"{fs[fl]}({a}, {b}, {L})")
            return c, False
        if lt == 'Float':
            if op == '/':
                return f"dv_fdiv({a}, {b}, {L})", False
            c = f"({a} {op} {b})"
            if fl:
                c = self.alt(e, f"`{op}` becomes `{fl}`", c, f"({a} {fl} {b})")
            return c, False
        if lt == 'Text':
            return f"dv_text_concat({a}, {b})", True
        return f"{self.cty(lt)}_concat({a}, {b})", True

    def g_call(self, e):
        t = e.ty
        if e.builtin:
            c = self.builtin(e)
        else:
            c = f"F_{e.name}({', '.join(self.val(a) for a in e.args)})"
        if e.effectful and t not in ('Void', 'Never'):
            x = self.newtemp(t) if self.heap(t) else self.plain(t)
            self.emit(f"{x} = {c};")
            return x, False
        return c, True

    def builtin(self, e):
        n, L = e.name, self.loc(e)
        if n == 'or_else':  # lazy: the default is computed only when the Opt is none
            o = self.plain(e.args[0].ty, self.val(e.args[0]))
            x = self.plain(e.ty)
            self.emit(f"if ({o}.has) {{ {x} = {self.copy(e.ty, o + '.v')}; }} else {{")
            d = self.val(e.args[1], sink=True)
            self.emit(f"{x} = {d}; }}")
            return x
        a = [self.val(x) for x in e.args]
        if n in SIMPLE:
            return SIMPLE[n].format(*a, L=L)
        t0 = e.args[0].ty
        if n == 'len':
            return f"({a[0]}).n"
        if n == 'show':
            b = self.name('b')
            self.decl(f"dv_buf {b} = {{0}};")
            self.emit(self.show(t0, '&' + b, a[0]))
            return f"dv_buf_take(&{b})"
        if n == 'slice':
            f = 'dv_text_slice' if t0 == 'Text' else self.cty(t0) + '_slice'
            return f"{f}({a[0]}, {a[1]}, {a[2]}, {L})"
        if n == 'abs':
            return f"dv_abs_i({a[0]}, {L})" if t0 == 'Int' else f"fabs({a[0]})"
        if n in ('min', 'max'):
            return f"dv_{n}_i({a[0]}, {a[1]})" if t0 == 'Int' else f"f{n}({a[0]}, {a[1]})"
        raise AssertionError(n)

    def lv(self, e):
        if e.k == 'Name':
            return 'v_' + e.name
        if e.k == 'Field':
            return f"{self.lv(e.e)}.f_{e.name}"
        return f"(*{self.cty(e.e.ty)}_at(&{self.lv(e.e)}, {self.val(e.i)}, {self.loc(e)}))"

    # -- statements
    def block(self, stmts, kind='block', head=(), cov=None, tail=None):
        self.emit('{')
        self.scopes.append(Scope(kind))
        for h in head:
            self.emit(h)
        if cov is not None:
            self.emit(f"DV_COV({cov});")
        for s in stmts:
            self.stmt(s)
        self.frees(self.scopes[-1:])
        if tail:
            self.emit(tail)
        self.scopes.pop()
        self.emit('}')

    def stmt(self, s):
        ob, od = self.buf, self.declbuf
        self.buf, self.declbuf = [], []
        self.stmt_(s)
        lines = self.declbuf + self.buf
        self.buf, self.declbuf = ob, od
        self.buf.extend(lines)

    def stmt_(self, s):
        k = s.k
        if k == 'Let':
            x = 'v_' + s.name
            self.emit(f"{self.cty(s.ty)} {x} = {self.val(s.e, sink=True)};")
            if self.heap(s.ty):
                self.scopes[-1].owned.append((x, s.ty))
        elif k == 'Assign':
            if self.mut_on:   # mutant: this assignment does not happen
                self.emit(f"if (dv_bm != {self.newmut(s, 'this assignment is removed')}) {{")
            t = s.target.ty
            nv = self.plain(t, self.val(s.e, sink=True))
            lv = self.lv(s.target)
            if s.target.k == 'Name':
                self.emit(self.free(t, lv))
                self.emit(f"{lv} = {nv};")
            else:
                p = self.name('p')
                self.emit(f"{self.cty(t)}* {p} = &{lv};")
                self.emit(self.free(t, '*' + p))
                self.emit(f"*{p} = {nv};")
            if self.mut_on:
                self.emit("}")
        elif k == 'MethodStmt':
            c = s.call
            X = self.cty(c.e.ty)
            if self.mut_on:
                self.emit(f"if (dv_bm != {self.newmut(s, f'this `.{c.name}` is removed')}) {{")
            if c.name == 'push':
                v = self.plain(c.e.ty[1], self.val(c.args[0], sink=True))
                self.emit(f"{X}_push(&{self.lv(c.e)}, {v});")
            else:
                self.emit(f"{X}_pop(&{self.lv(c.e)}, {self.loc(c)});")
            if self.mut_on:
                self.emit("}")
        elif k == 'If':
            self.emit(f"if ({self.val(s.cond)})")
            self.block(s.then, cov=self.newcov(s.line, 'if', s.then))
            if s.els is not None:
                self.emit("else")
                chain = len(s.els) == 1 and s.els[0].k == 'If'
                self.block(s.els, cov=None if chain else self.newcov(s.els[0].line if s.els else s.line, 'else', s.els))
        elif k == 'While':
            self.emit("while (1) {")
            self.scopes.append(Scope('loop'))
            ob, od = self.buf, self.declbuf
            self.buf, self.declbuf = [], []
            c = self.val(s.cond)
            lines = self.declbuf + self.buf
            self.buf, self.declbuf = ob, od
            self.buf.extend(lines)
            self.emit(f"if (!({c})) {{")
            self.frees(self.scopes[-1:])
            self.emit("break; }")
            self.emit(f"DV_COV({self.newcov(s.line, 'while', s.body)}); DV_STEP;")
            for st in s.body:
                self.stmt(st)
            self.frees(self.scopes[-1:])
            self.scopes.pop()
            self.emit("}")
        elif k == 'ForRange':
            a = self.plain('Int', self.val(s.a))
            b = self.plain('Int', self.val(s.b))
            v = 'v_' + s.name
            self.emit(f"for (int64_t {v} = {a}; {v} < {b}; {v}++)")
            self.block(s.body, 'loop', head=["DV_STEP;"], cov=self.newcov(s.line, 'for', s.body))
        elif k == 'ForIn':
            t = s.e.ty
            c = self.val(s.e, sink=s.snapshot)
            it = self.newtemp(t) if s.snapshot else self.plain(t)
            self.emit(f"{it} = {c};")
            i = self.name('i')
            self.emit(f"for (int64_t {i} = 0; {i} < {it}.n; {i}++)")
            self.block(s.body, 'loop', head=[f"{self.cty(s.elem)} v_{s.name} = {it}.p[{i}]; DV_STEP;"],
                       cov=self.newcov(s.line, 'for', s.body))
        elif k == 'Match':
            t = s.mty
            c = self.val(s.e, sink=s.snapshot)
            m = self.newtemp(t) if (s.snapshot and self.heap(t)) else self.plain(t)
            self.emit(f"{m} = {c};")
            first = True
            for pat, body in s.arms:
                head = []
                if pat.k == 'PSome':
                    cond = f"{m}.has"
                    head = [f"{self.cty(t[1])} v_{pat.name} = {m}.v;"]
                elif pat.k == 'PElse':
                    cond = "1"
                elif pat.k == 'PLit':
                    cond = ' || '.join(self.eq(t, m, self.g(lit)[0]) for lit in pat.lits)
                elif pat.k == 'PNone':
                    cond = f"!{m}.has"
                else:
                    cond = f"{m}.tag == {pat.idx}"
                    fts = dict(self.ck.variants[pat.name][2])
                    head = [f"{self.cty(fts[b])} v_{b} = {m}.u.V_{pat.name}.f_{b};" for b in pat.binds if b != '_']
                self.emit(f"{'if' if first else 'else if'} ({cond})")
                self.block(body, head=head, cov=self.newcov(pat.line, 'match arm', body))
                first = False
        elif k == 'Return':
            if s.e is None:
                self.frees(self.scopes)
                self.emit("return;")
                return
            t, skip = self.fn.ret, None
            e = s.e
            if e.k == 'Name' and e.ref == 'var' and any(x == 'v_' + e.name for sc in self.scopes for x, _ in sc.owned):
                r = skip = 'v_' + e.name
            else:
                r = self.plain(t, self.val(e, sink=True))
            self.frees(self.scopes, skip)
            self.emit(f"return {r};")
        elif k in ('Break', 'Continue'):
            i = len(self.scopes) - 1
            while self.scopes[i].kind != 'loop':
                i -= 1
            self.frees(self.scopes[i:])
            self.emit("break;" if k == 'Break' else "continue;")
        elif k == 'ExprStmt':
            c, _ = self.g(s.e)
            self.emit(c + ";")
        else:
            raise AssertionError(k)

    # -- functions
    def start(self):
        self.buf, self.declbuf, self.scopes = [], [], []

    def gen_fn(self, f, protos, bodies):
        n = f.node
        self.fn = f
        params = ', '.join(f"{self.cty(t)} v_{p}" for p, t in f.params) or 'void'
        ret = self.cty(f.ret)
        mutable = self.mode == 'test' and f.ret != 'Void' and not f.uses and f.name != 'main'
        contract = bool(n.needs or n.gives) or mutable
        impl = f"F_{f.name}__impl" if contract else f"F_{f.name}"
        protos.append(f"static {ret} {impl}({params});")
        self.start()
        self.emit(f"static {ret} {impl}({params})")
        tail = None if f.ret == 'Void' else f'dv_trap("reached the end of {f.name} without return", {self.loc(n)});'
        self.mut_on = self.mode == 'test' and not f.uses and f.name != 'main' and bool(n.exs)
        head = [f"DV_ENTER({self.fn_id[f.name]});"] if self.mode == 'test' else []
        self.block(n.body, 'fn', head=head, cov=self.newcov(n.line, 'fn body', n.body), tail=tail)
        self.mut_on = False
        bodies.extend(self.buf)
        if not contract:
            return
        protos.append(f"static {ret} F_{f.name}({params});")
        self.start()
        self.scopes.append(Scope('fn'))
        for e in n.needs:
            c = self.val(e)
            self.emit(f"if (!({c})) dv_trap({cstr('need failed: ' + e.src + ' — in call to ' + f.name)}, {self.loc(e)});")
        call = f"{impl}({', '.join('v_' + p for p, _ in f.params)})"
        if f.ret == 'Void':
            self.emit(call + ";")
        else:
            if mutable:
                k = self.fn_id[f.name]
                self.emit(f"if (dv_depth[{k}] == 0) dv_ran[{k}]++;")
                self.emit(f"dv_depth[{k}]++;")
                self.emit(f"{ret} v_out = {call};")
                self.emit(f"dv_depth[{k}]--;")
            else:
                self.emit(f"{ret} v_out = {call};")
            for e in n.gives:
                c = self.val(e)
                self.emit(f"if (!({c})) dv_trap({cstr('give failed: ' + e.src + ' — result of ' + f.name)}, {self.loc(e)});")
            if mutable:
                t = f.ret
                self.emit(f"if (dv_mut_mode && dv_mut_target == {k} && dv_depth[{k}] == 0) {{")
                self.emit(f"  {ret} m = dv_mut_mode == 1 ? ({ret}){{0}} : {pf(self.reg(t))}_perturb(v_out);")
                self.emit(f"  if (!{self.eq(t, 'm', 'v_out')}) {{ {self.free(t, 'v_out')} v_out = m; dv_mut_fired++; }}")
                self.emit(f"  else {{ {self.free(t, 'm')} }}")
                self.emit("}")
        self.frees(self.scopes)
        if f.ret != 'Void':
            self.emit("return v_out;")
        bodies.append(f"static {ret} F_{f.name}({params}) {{")
        bodies.extend(self.declbuf + self.buf)
        bodies.append("}")

    def gen_ex(self, i, e, bodies):
        self.start()
        self.scopes.append(Scope('fn'))
        if e.k == 'Bin' and e.op == '==':
            t = e.l.ty
            l, r = self.val(e.l), self.val(e.r)
            if not self.heap(t):
                l, r = self.plain(t, l), self.plain(t, r)
            self.emit(f"bool ok = {self.eq(t, l, r)};")
            self.emit("if (!ok && !dv_quiet) { dv_buf g = {0}, w = {0}; " + self.show(t, '&g', l) + ' ' + self.show(t, '&w', r) +
                      f' printf("EX\\t{i}\\tfail\\t%s\\t%s\\n", g.p, w.p); free(g.p); free(w.p); }}')
        else:
            self.emit(f"bool ok = {self.val(e)};")
            self.emit(f'if (!ok && !dv_quiet) printf("EX\\t{i}\\tfail\\n");')
        self.frees(self.scopes)
        self.emit("return ok;")
        bodies.append(f"static bool EX_{i}(void) {{")
        bodies.extend(self.declbuf + self.buf)
        bodies.append("}")

    def program(self):
        ck = self.ck
        consts = []
        for c in ck.consts.values():
            if c.ty == 'Text':
                consts.append(f"static const dv_text C_{c.name} = {{(char*){cstr(c.value)}, {len(c.value.encode('utf-8'))}}};")
            elif c.ty == 'Int':
                consts.append(f"static const int64_t C_{c.name} = ({'-' if c.value < 0 else ''}INT64_C({abs(c.value)}));")
            elif c.ty == 'Float':
                consts.append(f"static const double C_{c.name} = {c.value!r};")
            else:
                consts.append(f"static const bool C_{c.name} = {'true' if c.value else 'false'};")
        protos, bodies = [], []
        self.fn_id = {name: k for k, name in enumerate(ck.fns)}
        for f in ck.fns.values():
            self.gen_fn(f, protos, bodies)
        self.fn = None
        main = []
        if self.mode == 'test':
            for f in ck.fns.values():
                if not f.uses and f.name != 'main':
                    for e in f.node.exs:
                        self.exs.append((f.name, e))
            for i, (_, e) in enumerate(self.exs):
                self.gen_ex(i, e, bodies)
            E, F, M = len(self.exs), len(ck.fns), len(self.bmuts)
            main.append(f"static bool (*dv_ex_tab[{E + 1}])(void) = {{" + ', '.join(f"EX_{i}" for i in range(E)) + (", " if E else "") + "0};")
            main.append(f"static const int dv_ex_fn[{E + 1}] = {{" + ', '.join(str(self.fn_id[fn]) for fn, _ in self.exs) + (", " if E else "") + "0};")
            main.append(f"static const int dv_bm_fn[{M + 2}] = {{0, " + ', '.join(str(self.fn_id[m['fn']]) for m in self.bmuts) + (", " if M else "") + "0};")
            main.append(f"static unsigned char dv_touch[{E + 1}][{F + 1}], dv_exok[{E + 1}]; static long long dv_exsteps[{E + 1}];")
            main.append("static bool dv_try(int i) { volatile bool passed = false; if (setjmp(dv_jmp) == 0) passed = dv_ex_tab[i](); return passed; }")
            main.append("int main(void) { dv_test_mode = 1;")
            main.append(f"for (int i = 0; i < {E}; i++) {{ int k = dv_ex_fn[i];")
            main.append('  memset(dv_ran, 0, sizeof dv_ran); memset(dv_seen, 0, sizeof dv_seen); dv_steps = 0;')
            main.append('  if (setjmp(dv_jmp) == 0) { if (dv_ex_tab[i]()) { printf("EX\\t%d\\tok\\n", i); '
                        'memcpy(dv_touch[i], dv_seen, sizeof dv_seen); dv_exsteps[i] = dv_steps; dv_exok[i] = 1; '
                        'if (!dv_ran[k]) printf("NORUN\\t%d\\n", i); dv_mutate(i, k, dv_ex_tab[i]); } } '
                        'else printf("EX\\t%d\\ttrap\\t%s\\n", i, dv_trap_msg); fflush(stdout); }')
            main.append(f'for (int i = 0; i < {len(self.cov)}; i++) printf("COV\\t%d\\t%lld\\n", i, dv_cov[i]);')
            # body mutants: switch on one small change at a time; an example "notices" it if it stops passing
            main.append("fflush(stdout); dv_quiet = 1; clock_t t0 = clock();")
            main.append(f"for (int m = 1; m <= {M}; m++) {{")
            main.append('  if ((double)(clock() - t0) / CLOCKS_PER_SEC > 20.0) { printf("BMSTOP\\t%d\\n", m); break; }')
            main.append("  int killed = 0, tried = 0;")
            main.append(f"  for (int i = 0; i < {E} && !killed; i++) {{")
            main.append("    if (!dv_exok[i] || !dv_touch[i][dv_bm_fn[m]]) continue;")
            main.append("    tried++; dv_bm = m; dv_fuel = dv_exsteps[i] * 10 + 100000; memset(dv_depth, 0, sizeof dv_depth);")
            main.append("    if (!dv_try(i)) killed = 1;")
            main.append("    dv_bm = 0;")
            main.append("  }")
            main.append('  printf("BM\\t%d\\t%d\\t%d\\n", m, killed, tried);')
            main.append("}")
            main.append("return 0; }")
            covdef = [f"static long long dv_cov[{len(self.cov) + 1}];", "#define DV_COV(i) (dv_cov[i]++)",
                      f"static unsigned char dv_seen[{len(ck.fns) + 1}];",
                      "static int dv_bm = 0; static long long dv_steps = 0, dv_fuel = 0;",
                      '#define DV_STEP do { dv_steps++; if (dv_bm && --dv_fuel < 0) dv_trap("mutant ran too long", "mutant"); } while (0)',
                      "#define DV_ENTER(k) do { dv_seen[k] = 1; DV_STEP; } while (0)"]
            mut = [f"static int dv_depth[{len(ck.fns) + 1}];", f"static int dv_ran[{len(ck.fns) + 1}];",
                   "static int dv_mut_mode, dv_mut_target = -1, dv_mut_fired, dv_quiet;",
                   # run example i twice more, with f's outermost result replaced by the zero / perturbed value
                   "static void dv_mutate(int i, int target, bool (*ex)(void)) {",
                   "  int fired = 0, survived = 0;",
                   "  for (int mode = 1; mode <= 2; mode++) {",
                   "    memset(dv_depth, 0, sizeof dv_depth); dv_mut_mode = mode; dv_mut_target = target; dv_mut_fired = 0; dv_quiet = 1;",
                   "    bool passed = false, trapped = false;",
                   "    if (setjmp(dv_jmp) == 0) passed = ex(); else trapped = true;",
                   "    if (dv_mut_fired && !trapped) { fired++; if (passed) survived++; }",
                   "  }",
                   "  dv_mut_mode = 0; dv_mut_target = -1; dv_quiet = 0; memset(dv_depth, 0, sizeof dv_depth);",
                   '  printf("MUT\\t%d\\t%d\\t%d\\n", i, fired, survived);',
                   "}"]
        else:
            main.append("int main(int argc, char** argv) { dv_argc = argc; dv_argv = argv; F_main(); return 0; }")
            covdef = ["#define DV_COV(i)", "#define DV_STEP"]
            mut = []
        types = self.gen_types()
        pre = open(os.path.join(HERE, 'rt_pre.h')).read()
        post = open(os.path.join(HERE, 'rt_post.h')).read()
        if any('video' in f.uses for f in ck.fns.values()):
            post += '\n' + open(os.path.join(HERE, 'rt_video.h')).read()
        return '\n'.join([pre] + covdef + mut + types + [post] + consts + protos + bodies + main) + '\n'
