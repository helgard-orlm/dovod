// Dovod runtime, part 1 (before generated types).
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <setjmp.h>
#include <time.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
// UTF-8 <-> UTF-16 for paths and arguments (Windows APIs speak UTF-16, Dovod Text is UTF-8)
static wchar_t* dv_wide(const char* p, int64_t n) {
    int k = MultiByteToWideChar(CP_UTF8, 0, p, (int)n, NULL, 0);
    wchar_t* w = malloc(sizeof(wchar_t) * ((size_t)k + 1));
    if (!w) { fputs("out of memory\n", stderr); exit(70); }
    MultiByteToWideChar(CP_UTF8, 0, p, (int)n, w, k); w[k] = 0; return w;
}
static char* dv_narrow(const wchar_t* w) {
    int k = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    char* p = malloc((size_t)k + 1);
    if (!p) { fputs("out of memory\n", stderr); exit(70); }
    WideCharToMultiByte(CP_UTF8, 0, w, -1, p, k, NULL, NULL); p[k] = 0; return p;
}
#endif

typedef struct { char* p; int64_t n; } dv_text;
#define DV_LIT(s, n) ((dv_text){(char*)(s), (n)})

static int dv_test_mode = 0;
static jmp_buf dv_jmp;
static char dv_trap_msg[600];
static int dv_argc = 0;
static char** dv_argv = NULL;

static _Noreturn void dv_trap(const char* msg, const char* loc) {
    if (dv_test_mode) {
        snprintf(dv_trap_msg, sizeof dv_trap_msg, "%s (at %s)", msg, loc);
        longjmp(dv_jmp, 1);
    }
    fflush(stdout);
    fprintf(stderr, "trap: %s (at %s)\n", msg, loc);
    exit(70);
}
static _Noreturn void dv_trap_text(dv_text m, const char* loc) {
    char b[400];
    snprintf(b, sizeof b, "%.*s", (int)(m.n > 380 ? 380 : m.n), m.p);
    dv_trap(b, loc);
}
static _Noreturn void dv_trap_idx(int64_t i, int64_t n, const char* loc) {
    char b[120];
    snprintf(b, sizeof b, "index %lld out of range (length %lld)", (long long)i, (long long)n);
    dv_trap(b, loc);
}
static void* dv_alloc(size_t n) {
    void* p = malloc(n ? n : 1);
    if (!p) dv_trap("out of memory", "runtime");
    return p;
}
static FILE* dv_open_path(const char* p, int64_t n, int writing) {
#ifdef _WIN32
    wchar_t* w = dv_wide(p, n); FILE* f = _wfopen(w, writing ? L"wb" : L"rb"); free(w); return f;
#else
    char* cp = dv_alloc((size_t)n + 1); memcpy(cp, p, (size_t)n); cp[n] = 0;
    FILE* f = fopen(cp, writing ? "wb" : "rb"); free(cp); return f;
#endif
}

// ---- checked integer arithmetic
static inline int64_t dv_add(int64_t a, int64_t b, const char* loc) { int64_t r; if (__builtin_add_overflow(a, b, &r)) dv_trap("integer overflow in +", loc); return r; }
static inline int64_t dv_sub(int64_t a, int64_t b, const char* loc) { int64_t r; if (__builtin_sub_overflow(a, b, &r)) dv_trap("integer overflow in -", loc); return r; }
static inline int64_t dv_mul(int64_t a, int64_t b, const char* loc) { int64_t r; if (__builtin_mul_overflow(a, b, &r)) dv_trap("integer overflow in *", loc); return r; }
static inline int64_t dv_div(int64_t a, int64_t b, const char* loc) { if (b == 0) dv_trap("division by zero", loc); if (a == INT64_MIN && b == -1) dv_trap("integer overflow in /", loc); return a / b; }
static inline double dv_fdiv(double a, double b, const char* loc) { if (b == 0.0) dv_trap("division by zero", loc); return a / b; }
static inline int64_t dv_mod(int64_t a, int64_t b, const char* loc) { if (b == 0) dv_trap("division by zero in %", loc); if (b == -1) return 0; return a % b; }
static inline int64_t dv_neg(int64_t a, const char* loc) { if (a == INT64_MIN) dv_trap("integer overflow in negation", loc); return -a; }
static inline int64_t dv_abs_i(int64_t a, const char* loc) { return a < 0 ? dv_neg(a, loc) : a; }
static inline int64_t dv_min_i(int64_t a, int64_t b) { return a < b ? a : b; }
static inline int64_t dv_max_i(int64_t a, int64_t b) { return a > b ? a : b; }
static inline int64_t dv_trunc(double x, const char* loc) { if (!(x > -9.2233720368547e18 && x < 9.2233720368547e18)) dv_trap("trunc: Float does not fit Int", loc); return (int64_t)x; }

// ---- text
static dv_text dv_text_new(const char* p, int64_t n) { dv_text r; r.n = n; r.p = dv_alloc((size_t)n + 1); if (n) memcpy(r.p, p, (size_t)n); r.p[n] = 0; return r; }
static dv_text dv_text_copy(dv_text a) { return dv_text_new(a.p, a.n); }
static void dv_text_free(dv_text a) { free(a.p); }
static bool dv_text_eq(dv_text a, dv_text b) { return a.n == b.n && (a.n == 0 || memcmp(a.p, b.p, (size_t)a.n) == 0); }
static int dv_text_cmp(dv_text a, dv_text b) { int64_t m = a.n < b.n ? a.n : b.n; int c = m ? memcmp(a.p, b.p, (size_t)m) : 0; if (c) return c; return a.n < b.n ? -1 : (a.n > b.n ? 1 : 0); }
static dv_text dv_text_concat(dv_text a, dv_text b) { dv_text r; r.n = a.n + b.n; r.p = dv_alloc((size_t)r.n + 1); if (a.n) memcpy(r.p, a.p, (size_t)a.n); if (b.n) memcpy(r.p + a.n, b.p, (size_t)b.n); r.p[r.n] = 0; return r; }
static dv_text dv_text_slice(dv_text a, int64_t i, int64_t j, const char* loc) { if (i < 0 || j < i || j > a.n) dv_trap("slice bounds out of range", loc); return dv_text_new(a.p + i, j - i); }
static int64_t dv_find_raw(dv_text a, dv_text b) { if (b.n == 0) return 0; for (int64_t i = 0; i + b.n <= a.n; i++) if (memcmp(a.p + i, b.p, (size_t)b.n) == 0) return i; return -1; }
static bool dv_starts(dv_text a, dv_text b) { return b.n <= a.n && (b.n == 0 || memcmp(a.p, b.p, (size_t)b.n) == 0); }
static bool dv_ends(dv_text a, dv_text b) { return b.n <= a.n && (b.n == 0 || memcmp(a.p + a.n - b.n, b.p, (size_t)b.n) == 0); }
static dv_text dv_trim(dv_text a) { int64_t i = 0, j = a.n; while (i < j && (a.p[i] == ' ' || a.p[i] == '\t' || a.p[i] == '\n' || a.p[i] == '\r')) i++; while (j > i && (a.p[j-1] == ' ' || a.p[j-1] == '\t' || a.p[j-1] == '\n' || a.p[j-1] == '\r')) j--; return dv_text_new(a.p + i, j - i); }
static int64_t dv_byte(dv_text a, int64_t i, const char* loc) { if (i < 0 || i >= a.n) dv_trap_idx(i, a.n, loc); return (unsigned char)a.p[i]; }
static dv_text dv_from_byte(int64_t b, const char* loc) { if (b < 0 || b > 255) dv_trap("from_byte: value not in 0..255", loc); char c = (char)b; return dv_text_new(&c, 1); }

// ---- growable buffer (show, replace, join)
typedef struct { char* p; size_t n, cap; } dv_buf;
static void dv_buf_put(dv_buf* b, const char* s, size_t n) {
    if (b->n + n + 1 > b->cap) { b->cap = (b->n + n + 1) * 2; b->p = realloc(b->p, b->cap); if (!b->p) dv_trap("out of memory", "runtime"); }
    if (n) memcpy(b->p + b->n, s, n);
    b->n += n; b->p[b->n] = 0;
}
static void dv_buf_puts(dv_buf* b, const char* s) { dv_buf_put(b, s, strlen(s)); }
static dv_text dv_buf_take(dv_buf* b) { dv_text r; if (!b->p) return dv_text_new("", 0); r.p = b->p; r.n = (int64_t)b->n; b->p = NULL; b->n = b->cap = 0; return r; }
static dv_text dv_replace(dv_text a, dv_text from, dv_text to, const char* loc) {
    if (from.n == 0) dv_trap("replace: empty pattern", loc);
    dv_buf b = {0}; int64_t i = 0;
    while (i < a.n) {
        if (i + from.n <= a.n && memcmp(a.p + i, from.p, (size_t)from.n) == 0) { dv_buf_put(&b, to.p, (size_t)to.n); i += from.n; }
        else { dv_buf_put(&b, a.p + i, 1); i++; }
    }
    return dv_buf_take(&b);
}
static void dv_int_show(dv_buf* b, int64_t x) { char s[32]; snprintf(s, sizeof s, "%lld", (long long)x); dv_buf_puts(b, s); }
static void dv_float_show(dv_buf* b, double x) {
    char s[48]; snprintf(s, 40, "%.15g", x);
    if (strtod(s, NULL) != x) snprintf(s, 40, "%.17g", x);
    if (!strpbrk(s, ".eEni")) strcat(s, ".0");
    dv_buf_puts(b, s);
}
static void dv_bool_show(dv_buf* b, bool x) { dv_buf_puts(b, x ? "true" : "false"); }
static void dv_text_show(dv_buf* b, dv_text t) {
    dv_buf_put(b, "\"", 1);
    for (int64_t i = 0; i < t.n; i++) {
        char c = t.p[i];
        if (c == '"') dv_buf_put(b, "\\\"", 2);
        else if (c == '\\') dv_buf_put(b, "\\\\", 2);
        else if (c == '\n') dv_buf_put(b, "\\n", 2);
        else if (c == '\t') dv_buf_put(b, "\\t", 2);
        else if (c == '\r') dv_buf_put(b, "\\r", 2);
        else if (c == 0) dv_buf_put(b, "\\0", 2);
        else if ((unsigned char)c < 32 || c == 127) { char h[5]; snprintf(h, sizeof h, "\\x%02X", (unsigned char)c); dv_buf_put(b, h, 4); }
        else dv_buf_put(b, &c, 1);
    }
    dv_buf_put(b, "\"", 1);
}

// ---- mutants for the "an example must be able to fail" check
static inline int64_t dv_int_perturb(int64_t a) { return (a == INT64_MAX || a == -1) ? a - 1 : a + 1; }
static inline double dv_float_perturb(double a) { return a + 1.0; }
static inline bool dv_bool_perturb(bool a) { return !a; }
static dv_text dv_text_perturb(dv_text a) { return dv_text_concat(a, DV_LIT("\x01", 1)); }

// ---- effects with scalar results
static void dv_print(dv_text t) { if (t.n) fwrite(t.p, 1, (size_t)t.n, stdout); fputc('\n', stdout); fflush(stdout); }
static bool dv_write_file(dv_text path, dv_text data) {
    FILE* f = dv_open_path(path.p, path.n, 1);
    if (!f) return false;
    bool ok = data.n == 0 || fwrite(data.p, 1, (size_t)data.n, f) == (size_t)data.n;
    if (fclose(f) != 0) ok = false;
    return ok;
}
static int64_t dv_now_ms(void) {
#ifdef _WIN32
    FILETIME ft; GetSystemTimeAsFileTime(&ft);  // 100 ns since 1601
    uint64_t t = ((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    return (int64_t)((t - 116444736000000000ULL) / 10000);
#else
    struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts); return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
#endif
}
static int64_t dv_rand_int(int64_t lo, int64_t hi, const char* loc) {
    static uint64_t st = 0;  // xorshift64*, seeded from the clock (portable: no random()/srandom())
    if (lo > hi) dv_trap("rand_int: lo > hi", loc);
    if (!st) { st = (uint64_t)dv_now_ms() * 0x9E3779B97F4A7C15ULL ^ (uint64_t)clock() ^ (uint64_t)(uintptr_t)&st; if (!st) st = 1; }
    st ^= st >> 12; st ^= st << 25; st ^= st >> 27;
    uint64_t r = st * 2685821657736338717ULL;
    uint64_t span = (uint64_t)hi - (uint64_t)lo + 1;
    return span == 0 ? (int64_t)r : (int64_t)((uint64_t)lo + r % span);
}
