"""Dovod: lexer + parser. Source text -> list of top-level Nodes."""
import re
from collections import namedtuple

Tok = namedtuple('Tok', 'kind val file line col pos')


class Diag:
    """One diagnostic. `fix` is a concrete suggestion an agent can apply."""

    def __init__(self, code, file, line, col, msg, fix=None):
        self.code, self.file, self.line, self.col, self.msg, self.fix = code, file, line, col, msg, fix

    def to_dict(self):
        d = {'code': self.code, 'file': self.file, 'line': self.line, 'col': self.col, 'msg': self.msg}
        if self.fix:
            d['fix'] = self.fix
        return d

    def text(self):
        s = f"{self.file}:{self.line}:{self.col}: {self.code}: {self.msg}"
        if self.fix:
            s += f"\n    fix: {self.fix}"
        return s


class DvError(Exception):
    def __init__(self, diag):
        super().__init__(diag.text())
        self.diag = diag


class Node:
    def __init__(self, k, at, **kw):
        self.k = k
        self.file, self.line, self.col = at.file, at.line, at.col
        self.__dict__.update(kw)


KEYWORDS = {
    'fn', 'record', 'enum', 'const', 'use', 'let', 'var', 'if', 'else', 'while', 'for', 'in',
    'match', 'return', 'break', 'continue', 'and', 'or', 'not', 'true', 'false', 'none', 'some',
    'uses', 'need', 'give', 'ex', 'assume', 'with',
    'module', 'effect', 'native', 'extern',
}

TOK_RE = re.compile(r'''
 (?P<ws>[ \t\r]+)|(?P<com>//[^\n]*)|(?P<nl>\n)|
 (?P<float>\d+\.\d+(?:[eE][+-]?\d+)?)|(?P<int>\d+)|
 (?P<id>[A-Za-z_][A-Za-z0-9_]*)|
 (?P<str>"(?:[^"\\\n]|\\.)*")|(?P<chr>'(?:[^'\\\n]|\\.)')|
 (?P<op>->|=>|==|!=|<=|>=|\.\.|[-+*/%<>=(){}\[\],:.?])
''', re.X)

ESC = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', '0': '\0'}


def lex(src, file):
    toks, pos, line, lstart, depth = [], 0, 1, 0, 0
    n = len(src)
    while pos < n:
        m = TOK_RE.match(src, pos)
        col = pos - lstart + 1
        if not m:
            raise DvError(Diag('E-SYNTAX', file, line, col, f"unexpected character {src[pos]!r}"))
        kind, val = m.lastgroup, m.group()
        if kind == 'nl':
            if depth == 0 and toks and toks[-1].kind != 'nl':
                toks.append(Tok('nl', '\n', file, line, col, pos))
            line += 1
            lstart = m.end()
        elif kind in ('ws', 'com'):
            pass
        elif kind == 'id':
            if val in KEYWORDS:
                toks.append(Tok('kw', val, file, line, col, pos))
            elif val[0].isupper():
                toks.append(Tok('ty', val, file, line, col, pos))
            else:
                toks.append(Tok('id', val, file, line, col, pos))
        elif kind == 'str':
            body, out, i = val[1:-1], [], 0
            while i < len(body):
                c = body[i]
                if c == '\\':
                    e = body[i + 1]
                    if e == 'x':
                        hx = body[i + 2:i + 4]
                        if len(hx) != 2 or any(ch not in '0123456789abcdefABCDEF' for ch in hx) or int(hx, 16) > 0x7F:
                            raise DvError(Diag('E-SYNTAX', file, line, col, "`\\x` needs two hex digits 00..7F"))
                        out.append(chr(int(hx, 16)))
                        i += 4
                        continue
                    if e not in ESC:
                        raise DvError(Diag('E-SYNTAX', file, line, col, f"unknown escape \\{e}",
                                           'use one of \\n \\t \\r \\\\ \\" \\0 \\xNN'))
                    out.append(ESC[e])
                    i += 2
                else:
                    out.append(c)
                    i += 1
            toks.append(Tok('str', ''.join(out), file, line, col, pos))
        elif kind == 'chr':
            body = val[1:-1]
            ch = ESC.get(body[1]) if body[0] == '\\' else body
            if body[0] == '\\' and body[1] == "'":
                ch = "'"
            if ch is None or ord(ch) > 0x7F:
                raise DvError(Diag('E-SYNTAX', file, line, col, "a character literal is one ASCII character: 'a', '\\n', '\\\\', '\\''",
                                   "it is an Int (the byte value); for other bytes write the number"))
            toks.append(Tok('int', ord(ch), file, line, col, pos))
        elif kind == 'int':
            v = int(val)
            if v > 2 ** 63 - 1:
                raise DvError(Diag('E-SYNTAX', file, line, col, "integer literal does not fit Int (64-bit)"))
            toks.append(Tok('int', v, file, line, col, pos))
        elif kind == 'float':
            v = float(val)
            if v in (float('inf'),):
                raise DvError(Diag('E-SYNTAX', file, line, col, "float literal out of range"))
            toks.append(Tok('float', v, file, line, col, pos))
        else:
            if val in '([':
                depth += 1
            elif val in ')]':
                depth = max(0, depth - 1)
            toks.append(Tok('op', val, file, line, col, pos))
        pos = m.end()
    toks.append(Tok('nl', '\n', file, line, 1, pos))
    toks.append(Tok('eof', '', file, line, 1, pos))
    return toks


class Parser:
    def __init__(self, src, file):
        self.src, self.file = src, file
        self.t = lex(src, file)
        self.i = 0

    # -- helpers
    def peek(self):
        return self.t[self.i]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def at(self, kind, val=None):
        tok = self.t[self.i]
        return tok.kind == kind and (val is None or tok.val == val)

    def accept(self, kind, val=None):
        if self.at(kind, val):
            return self.next()
        return None

    def fail(self, msg, fix=None, tok=None):
        tok = tok or self.peek()
        raise DvError(Diag('E-SYNTAX', tok.file, tok.line, tok.col, msg, fix))

    def expect(self, kind, val=None):
        if self.at(kind, val):
            return self.next()
        tok = self.peek()
        want = repr(val) if val is not None else {'id': 'a lowercase name', 'ty': 'a Capitalized name',
                                                  'str': 'a string literal', 'nl': 'end of line'}.get(kind, kind)
        got = 'end of line' if tok.kind == 'nl' else ('end of file' if tok.kind == 'eof' else repr(tok.val))
        self.fail(f"expected {want}, found {got}")

    def skip_nl(self):
        while self.at('nl'):
            self.i += 1

    def end_stmt(self):
        if self.at('op', '}') or self.at('eof'):
            return
        self.expect('nl')
        self.skip_nl()

    # -- top level
    def parse_file(self):
        items = []
        self.skip_nl()
        while not self.at('eof'):
            tok = self.peek()
            if tok.kind != 'kw' or tok.val not in ('fn', 'record', 'enum', 'const', 'use', 'module', 'effect',
                                                   'native', 'extern'):
                self.fail("expected a top-level item: fn, record, enum, const or use")
            items.append(getattr(self, 'p_' + tok.val)())
            self.skip_nl()
        return items

    def p_use(self):
        at = self.next()
        if self.at('id'):                       # `use video` — a module
            return Node('Use', at, path=None, module=self.next().val)
        path = self.expect('str').val           # `use "file.dv"` — a file
        return Node('Use', at, path=path, module=None)

    # -- module files only
    def p_module(self):
        at = self.next()
        return Node('Module', at, name=self.expect('id').val)

    def p_effect(self):
        at = self.next()
        name = self.expect('id').val
        return Node('Effect', at, name=name, doc=self.expect('str').val)

    def p_native(self):
        at = self.next()
        n = Node('Native', at, path=self.expect('str').val, targets=[], links=[], gui=False)
        while True:
            self.skip_nl()
            tok = self.peek()
            if tok.kind != 'id' or tok.val not in ('targets', 'links', 'gui'):
                return n
            self.next()
            if tok.val == 'gui':
                n.gui = True
            elif tok.val == 'targets':
                n.targets.append(self.expect('id').val)
                while self.accept('op', ','):
                    n.targets.append(self.expect('id').val)
            else:
                n.links.append(self.expect('str').val)
                while self.accept('op', ','):
                    n.links.append(self.expect('str').val)

    def p_extern(self):
        self.next()
        if not self.at('kw', 'fn'):
            self.fail("expected `fn` after `extern`")
        return self.p_fn(extern=True)

    def p_fields(self, close):
        """name: Type, separated by ',' and/or newlines, until `close`."""
        fields = []
        self.skip_nl()
        while not self.at('op', close):
            name = self.expect('id')
            self.expect('op', ':')
            fields.append((name.val, self.p_type(), name))
            if not self.accept('op', ','):
                if not self.at('nl') and not self.at('op', close):
                    self.fail("expected ',' or end of line between fields")
            self.skip_nl()
        self.expect('op', close)
        return fields

    def p_record(self):
        at = self.next()
        name = self.expect('ty').val
        self.expect('op', '{')
        return Node('Record', at, name=name, fields=self.p_fields('}'))

    def p_enum(self):
        at = self.next()
        name = self.expect('ty').val
        self.expect('op', '{')
        variants = []
        self.skip_nl()
        while not self.at('op', '}'):
            vt = self.expect('ty')
            fields = []
            if self.accept('op', '('):
                fields = self.p_fields(')')
            variants.append((vt.val, fields, vt))
            self.accept('op', ',')
            self.skip_nl()
        self.expect('op', '}')
        return Node('Enum', at, name=name, variants=variants)

    def p_const(self):
        at = self.next()
        name = self.expect('id').val
        self.expect('op', ':')
        ty = self.p_type()
        self.expect('op', '=')
        return Node('Const', at, name=name, ty=ty, e=self.p_expr())

    def p_type(self):
        tok = self.expect('ty')
        if tok.val in ('List', 'Opt'):
            self.expect('op', '[')
            inner = self.p_type()
            self.expect('op', ']')
            return (tok.val, inner)
        return tok.val

    def clause_text(self, start_tok):
        return self.src[start_tok.pos:self.peek().pos].strip()

    def p_fn(self, extern=False):
        at = self.next()
        name = self.expect('id').val
        self.expect('op', '(')
        params = []
        while not self.at('op', ')'):
            pn = self.expect('id')
            self.expect('op', ':')
            params.append((pn.val, self.p_type(), pn))
            if not self.accept('op', ','):
                break
        self.expect('op', ')')
        ret = 'Void'
        if self.accept('op', '->'):
            ret = self.p_type()
        sig = self.src[at.pos:self.peek().pos].strip()
        uses, needs, gives, exs, assume = [], [], [], [], None
        while True:
            self.skip_nl()
            tok = self.peek()
            if tok.kind != 'kw' or tok.val not in ('uses', 'need', 'give', 'ex', 'assume'):
                break
            self.next()
            if tok.val == 'uses':
                while True:
                    u = self.expect('id')
                    uses.append((u.val, u))
                    if not self.accept('op', ','):
                        break
            elif tok.val == 'assume':
                assume = (self.expect('str').val, tok)
            else:
                st = self.peek()
                e = self.p_expr()
                e.src = self.clause_text(st)
                {'need': needs, 'give': gives, 'ex': exs}[tok.val].append(e)
        if self.at('op', '->'):
            self.fail("the return type goes right after the parameters, before `uses` and the clauses",
                      f"write `fn {name}(...) -> Type uses ...`")
        if extern:
            if self.at('op', '{'):
                self.fail("an `extern fn` has no body: its code is the module's native file")
            body = []
        else:
            body = self.p_block()
        end_line = self.t[self.i - 1].line
        return Node('Fn', at, name=name, params=params, ret=ret, uses=uses, needs=needs, gives=gives,
                    exs=exs, assume=assume, body=body, sig=('extern ' if extern else '') + sig, end_line=end_line,
                    extern=extern)

    # -- statements
    def p_block(self):
        self.expect('op', '{')
        self.skip_nl()
        stmts = []
        while not self.at('op', '}'):
            if self.at('eof'):
                self.fail("missing '}'")
            stmts.append(self.p_stmt())
        self.expect('op', '}')
        return stmts

    def p_stmt(self):
        tok = self.peek()
        if tok.kind == 'kw':
            v = tok.val
            if v in ('let', 'var'):
                self.next()
                name = self.expect('id').val
                ty = None
                if self.accept('op', ':'):
                    ty = self.p_type()
                self.expect('op', '=')
                self.skip_nl()
                s = Node('Let', tok, name=name, decl=ty, e=self.p_expr(), mut=(v == 'var'))
                self.end_stmt()
                return s
            if v == 'if':
                return self.p_if()
            if v == 'while':
                self.next()
                cond = self.p_expr()
                s = Node('While', tok, cond=cond, body=self.p_block())
                self.end_stmt()
                return s
            if v == 'for':
                self.next()
                name = self.expect('id').val
                self.expect('kw', 'in')
                a = self.p_expr()
                if self.accept('op', '..'):
                    b = self.p_expr()
                    s = Node('ForRange', tok, name=name, a=a, b=b, body=self.p_block())
                else:
                    s = Node('ForIn', tok, name=name, e=a, body=self.p_block())
                self.end_stmt()
                return s
            if v == 'match':
                self.next()
                e = self.p_expr()
                self.expect('op', '{')
                self.skip_nl()
                arms = []
                while not self.at('op', '}'):
                    pat = self.p_pattern()
                    self.expect('op', '=>')
                    arms.append((pat, self.p_block()))
                    self.skip_nl()
                self.expect('op', '}')
                s = Node('Match', tok, e=e, arms=arms)
                self.end_stmt()
                return s
            if v == 'return':
                self.next()
                e = None
                if not (self.at('nl') or self.at('op', '}')):
                    e = self.p_expr()
                s = Node('Return', tok, e=e)
                self.end_stmt()
                return s
            if v in ('break', 'continue'):
                self.next()
                s = Node('Break' if v == 'break' else 'Continue', tok)
                self.end_stmt()
                return s
        e = self.p_expr()
        if self.accept('op', '='):
            self.skip_nl()
            s = Node('Assign', tok, target=e, e=self.p_expr())
        elif e.k == 'Method':
            s = Node('MethodStmt', tok, call=e)
        else:
            s = Node('ExprStmt', tok, e=e)
        self.end_stmt()
        return s

    def p_if(self):
        tok = self.next()
        cond = self.p_expr()
        then = self.p_block()
        els = None
        if self.accept('kw', 'else'):
            if self.at('kw', 'if'):
                els = [self.p_if()]
                return Node('If', tok, cond=cond, then=then, els=els)
            els = self.p_block()
        self.end_stmt()
        return Node('If', tok, cond=cond, then=then, els=els)

    def p_lit(self):
        tok = self.peek()
        if self.accept('op', '-'):
            n = self.expect('int')
            return Node('Int', tok, v=-n.val)
        if tok.kind == 'int':
            self.next()
            return Node('Int', tok, v=tok.val)
        if tok.kind == 'str':
            self.next()
            return Node('Str', tok, v=tok.val)
        if tok.kind == 'kw' and tok.val in ('true', 'false'):
            self.next()
            return Node('Bool', tok, v=(tok.val == 'true'))
        self.fail("expected a literal pattern (a number, a text or true/false)")

    def p_pattern(self):
        tok = self.peek()
        if self.accept('kw', 'else'):
            return Node('PElse', tok)
        if tok.kind in ('int', 'str') or (tok.kind == 'op' and tok.val == '-') or \
                (tok.kind == 'kw' and tok.val in ('true', 'false')):
            lits = [self.p_lit()]
            while self.accept('op', ','):
                self.skip_nl()
                lits.append(self.p_lit())
            return Node('PLit', tok, lits=lits)
        if self.accept('kw', 'none'):
            return Node('PNone', tok)
        if self.accept('kw', 'some'):
            self.expect('op', '(')
            name = self.expect('id').val
            self.expect('op', ')')
            return Node('PSome', tok, name=name)
        vt = self.expect('ty')
        binds = []
        if self.accept('op', '('):
            while not self.at('op', ')'):
                binds.append(self.expect('id').val)
                if not self.accept('op', ','):
                    break
            self.expect('op', ')')
        return Node('PVariant', vt, name=vt.val, binds=binds)

    # -- expressions
    def p_expr(self):
        return self.p_or()

    def p_or(self):
        e = self.p_and()
        while self.at('kw', 'or'):
            tok = self.next()
            self.skip_nl()
            e = Node('Bin', tok, op='or', l=e, r=self.p_and())
        return e

    def p_and(self):
        e = self.p_not()
        while self.at('kw', 'and'):
            tok = self.next()
            self.skip_nl()
            e = Node('Bin', tok, op='and', l=e, r=self.p_not())
        return e

    def p_not(self):
        if self.at('kw', 'not'):
            tok = self.next()
            return Node('Un', tok, op='not', e=self.p_not())
        return self.p_cmp()

    def p_cmp(self):
        e = self.p_add()
        tok = self.peek()
        if tok.kind == 'op' and tok.val in ('==', '!=', '<', '<=', '>', '>='):
            self.next()
            self.skip_nl()
            e = Node('Bin', tok, op=tok.val, l=e, r=self.p_add())
            nt = self.peek()
            if nt.kind == 'op' and nt.val in ('==', '!=', '<', '<=', '>', '>='):
                self.fail("comparisons do not chain", "write `a < b and b < c`")
        return e

    def p_add(self):
        e = self.p_mul()
        while self.peek().kind == 'op' and self.peek().val in ('+', '-'):
            tok = self.next()
            self.skip_nl()
            e = Node('Bin', tok, op=tok.val, l=e, r=self.p_mul())
        return e

    def p_mul(self):
        e = self.p_unary()
        while self.peek().kind == 'op' and self.peek().val in ('*', '/', '%'):
            tok = self.next()
            self.skip_nl()
            e = Node('Bin', tok, op=tok.val, l=e, r=self.p_unary())
        return e

    def p_unary(self):
        if self.at('op', '-'):
            tok = self.next()
            return Node('Un', tok, op='-', e=self.p_unary())
        return self.p_postfix()

    def p_args(self):
        """Returns (args, names): names[i] is the parameter name written at the call (`f(x, lo: 0)`) or None."""
        args, names = [], []
        while not self.at('op', ')'):
            nm = None
            if self.at('id') and self.t[self.i + 1].kind == 'op' and self.t[self.i + 1].val == ':':
                nm = self.next().val
                self.next()
            names.append(nm)
            args.append(self.p_expr())
            if not self.accept('op', ','):
                break
        self.expect('op', ')')
        return args, names

    def p_postfix(self):
        e = self.p_primary()
        while True:
            tok = self.peek()
            if self.accept('op', '.'):
                name = self.expect('id').val
                if self.accept('op', '('):
                    margs, mnames = self.p_args()
                    if any(mnames):
                        self.fail("`.push`/`.pop` take no named arguments", tok=tok)
                    e = Node('Method', tok, e=e, name=name, args=margs)
                else:
                    e = Node('Field', tok, e=e, name=name)
            elif self.accept('op', '['):
                idx = self.p_expr()
                self.expect('op', ']')
                e = Node('Index', tok, e=e, i=idx)
            elif self.accept('kw', 'with'):
                self.expect('op', '{')
                self.skip_nl()
                fields = []
                while not self.at('op', '}'):
                    fn = self.expect('id')
                    self.expect('op', ':')
                    fields.append((fn.val, self.p_expr()))
                    if not self.accept('op', ','):
                        if not self.at('nl') and not self.at('op', '}'):
                            self.fail("expected ',' or end of line between fields")
                    self.skip_nl()
                self.expect('op', '}')
                e = Node('With', tok, e=e, fields=fields)
            else:
                return e

    def p_primary(self):
        tok = self.next()
        k, v = tok.kind, tok.val
        if k == 'int':
            return Node('Int', tok, v=v)
        if k == 'float':
            return Node('Float', tok, v=v)
        if k == 'str':
            return Node('Str', tok, v=v)
        if k == 'kw' and v in ('true', 'false'):
            return Node('Bool', tok, v=(v == 'true'))
        if k == 'kw' and v == 'none':
            return Node('NoneLit', tok)
        if k == 'kw' and v == 'some':
            self.expect('op', '(')
            e = self.p_expr()
            self.expect('op', ')')
            return Node('Some', tok, e=e)
        if k == 'id':
            if self.accept('op', '('):
                cargs, cnames = self.p_args()
                return Node('Call', tok, name=v, args=cargs, argnames=cnames)
            return Node('Name', tok, name=v)
        if k == 'ty':
            fields = []
            if self.accept('op', '('):
                while not self.at('op', ')'):
                    fn = self.expect('id')
                    if not self.at('op', ':'):
                        self.fail(f"constructor fields are named: `{v}({fn.val}: value, ...)`")
                    self.next()
                    fields.append((fn.val, self.p_expr()))
                    if not self.accept('op', ','):
                        break
                self.expect('op', ')')
            return Node('Ctor', tok, name=v, fields=fields)
        if k == 'op' and v == '(':
            e = self.p_expr()
            self.expect('op', ')')
            return e
        if k == 'op' and v == '[':
            items = []
            while not self.at('op', ']'):
                items.append(self.p_expr())
                if not self.accept('op', ','):
                    break
            self.expect('op', ']')
            return Node('ListLit', tok, items=items)
        if k == 'op' and v == '?':
            return Node('Hole', tok, name=self.expect('id').val)
        got = 'end of line' if k == 'nl' else repr(v)
        self.fail(f"expected an expression, found {got}", tok=tok)


def parse(src, file):
    return Parser(src, file).parse_file()
