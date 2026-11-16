// Dovod runtime, part 2 (needs generated O_Int, O_Float, O_Text, L_Text).
static O_Int dv_find(dv_text a, dv_text b) { int64_t i = dv_find_raw(a, b); O_Int r; r.has = i >= 0; r.v = i >= 0 ? i : 0; return r; }
static L_Text dv_split(dv_text a, dv_text sep, const char* loc) {
    if (sep.n == 0) dv_trap("split: empty separator", loc);
    L_Text r = {0}; int64_t start = 0, i = 0;
    while (i + sep.n <= a.n) {
        if (memcmp(a.p + i, sep.p, (size_t)sep.n) == 0) { L_Text_push(&r, dv_text_new(a.p + start, i - start)); i += sep.n; start = i; }
        else i++;
    }
    L_Text_push(&r, dv_text_new(a.p + start, a.n - start));
    return r;
}
static dv_text dv_join(L_Text xs, dv_text sep) {
    dv_buf b = {0};
    for (int64_t i = 0; i < xs.n; i++) { if (i) dv_buf_put(&b, sep.p, (size_t)sep.n); dv_buf_put(&b, xs.p[i].p, (size_t)xs.p[i].n); }
    return dv_buf_take(&b);
}
static O_Int dv_parse_int(dv_text t) {
    O_Int r = {0}; int64_t i = 0, v = 0; bool neg = false;
    if (t.n == 0) return r;
    if (t.p[0] == '-' || t.p[0] == '+') { neg = t.p[0] == '-'; i = 1; if (t.n == 1) return r; }
    for (; i < t.n; i++) {
        if (t.p[i] < '0' || t.p[i] > '9') return r;
        int d = t.p[i] - '0';
        if (__builtin_mul_overflow(v, 10, &v)) return r;
        if (neg ? __builtin_sub_overflow(v, d, &v) : __builtin_add_overflow(v, d, &v)) return r;
    }
    r.has = true; r.v = v; return r;
}
static O_Float dv_parse_float(dv_text t) {
    O_Float r = {0};
    if (t.n == 0 || t.n > 400 || t.p[0] == ' ' || t.p[0] == '\t' || t.p[0] == '\n') return r;
    bool digit = false;
    for (int64_t i = 0; i < t.n; i++) { char c = t.p[i]; if (c >= '0' && c <= '9') digit = true; else if (!strchr("+-.eE", c)) return r; }
    if (!digit) return r;
    char b[402]; memcpy(b, t.p, (size_t)t.n); b[t.n] = 0;
    char* end; double v = strtod(b, &end);
    if (end != b + t.n) return r;
    r.has = true; r.v = v; return r;
}
static O_Text dv_read_line(void) {
    O_Text r = {0}; dv_buf b = {0}; int c; bool any = false;
    while ((c = fgetc(stdin)) != EOF) { any = true; if (c == '\n') break; char ch = (char)c; dv_buf_put(&b, &ch, 1); }
    if (!any) { free(b.p); return r; }
    r.has = true; r.v = dv_buf_take(&b); return r;
}
static O_Text dv_read_file(dv_text path) {
    O_Text r = {0};
    FILE* f = dv_open_path(path.p, path.n, 0);
    if (!f) return r;
    dv_buf b = {0}; char chunk[65536]; size_t n;
    while ((n = fread(chunk, 1, sizeof chunk, f)) > 0) dv_buf_put(&b, chunk, n);
    bool bad = ferror(f); fclose(f);
    if (bad) { free(b.p); return r; }
    r.has = true; r.v = dv_buf_take(&b); return r;
}
static L_Text dv_args(void) {
    L_Text r = {0};
#ifdef _WIN32
    int wn = 0; wchar_t** wa = CommandLineToArgvW(GetCommandLineW(), &wn);  // argv in UTF-8, not the ANSI code page
    if (wa) {
        for (int i = 1; i < wn; i++) { char* u = dv_narrow(wa[i]); L_Text_push(&r, dv_text_new(u, (int64_t)strlen(u))); free(u); }
        LocalFree(wa);
        return r;
    }
#endif
    for (int i = 1; i < dv_argc; i++) L_Text_push(&r, dv_text_new(dv_argv[i], (int64_t)strlen(dv_argv[i])));
    return r;
}
