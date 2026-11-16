// native side of module `demo`. Arguments are borrowed (do not free, do not keep); results are owned by the caller.
static int64_t dv_demo_twice(int64_t n) { return n * 2; }
static dv_text dv_demo_shout(dv_text t) {
    dv_text r = dv_text_new(t.p, t.n);
    for (int64_t i = 0; i < r.n; i++) if (r.p[i] >= 'a' && r.p[i] <= 'z') r.p[i] -= 32;
    return r;
}
static O_Text dv_demo_find(dv_text t) { O_Text r = {0}; if (t.n > 0) { r.has = true; r.v = dv_text_new(t.p, 1); } return r; }
static L_Text dv_demo_words(void) { L_Text r = {0}; L_Text_push(&r, dv_text_new("one", 3)); L_Text_push(&r, dv_text_new("two", 3)); return r; }
static int64_t dv_demo_level(int64_t pct) { return pct; }
static double dv_demo_hypot(double a, double b) { return sqrt(a * a + b * b); }
