// Module `web`, native side: a single-threaded HTTP/1.1 server inside the program. Sockets are served
// only inside web_next / web_reply, so the program owns the loop. Every request is answered once and the
// connection is closed (no keep-alive): simple, and enough for a home service or an API.
#ifndef DV_SOCK_H
#define DV_SOCK_H
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET dv_sock;
#define DV_BADSOCK INVALID_SOCKET
#define dv_closesock closesocket
#define DV_NOSIG 0
#else
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
typedef int dv_sock;
#define DV_BADSOCK (-1)
#define dv_closesock close
#define DV_NOSIG MSG_NOSIGNAL
#endif
#endif

#define DV_WEB_CL 48          // + the listening socket stays under Windows' FD_SETSIZE (64)
#define DV_WEB_MAXHDR 65536
#define DV_WEB_MAXBODY (64 * 1024 * 1024)
typedef struct { dv_sock s; char* buf; int64_t len, cap; int64_t id; double deadline; char peer[64]; } dv_web_client;
static dv_sock dv_web_ls = DV_BADSOCK;
static dv_web_client dv_web_cl[DV_WEB_CL];
static int64_t dv_web_nextid = 1;
static int dv_web_port = 0;

static double dv_web_now(void) {
#ifdef _WIN32
    return (double)GetTickCount64() / 1000.0;
#else
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
#endif
}
static void dv_web_sendall(dv_sock s, const char* p, size_t n) {
    while (n > 0) { int k = (int)send(s, p, (int)(n > 1 << 20 ? 1 << 20 : n), DV_NOSIG); if (k <= 0) return; p += k; n -= (size_t)k; }
}
static void dv_web_drop(dv_web_client* c) { dv_closesock(c->s); free(c->buf); c->s = DV_BADSOCK; c->buf = NULL; c->len = c->cap = 0; c->id = 0; }
static void dv_web_plain(dv_web_client* c, const char* status) {
    char h[200]; int k = snprintf(h, sizeof h, "HTTP/1.1 %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", status);
    dv_web_sendall(c->s, h, (size_t)k); dv_web_drop(c);
}

static O_Int dv_web_listen(int64_t port, bool lan) {
    O_Int none = {0};
    if (dv_web_ls != DV_BADSOCK) { O_Int r = { true, dv_web_port }; return r; }
#ifdef _WIN32
    WSADATA w; if (WSAStartup(MAKEWORD(2, 2), &w) != 0) return none;
#endif
    for (int i = 0; i < DV_WEB_CL; i++) { dv_web_cl[i].s = DV_BADSOCK; dv_web_cl[i].buf = NULL; }
    dv_sock s = socket(AF_INET, SOCK_STREAM, 0);
    if (s == DV_BADSOCK) return none;
    int one = 1; setsockopt(s, SOL_SOCKET, SO_REUSEADDR, (const char*)&one, sizeof one);
    struct sockaddr_in a; memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = htonl(lan ? INADDR_ANY : INADDR_LOOPBACK);
    if (bind(s, (struct sockaddr*)&a, sizeof a) != 0 || listen(s, 64) != 0) { dv_closesock(s); return none; }
    socklen_t al = sizeof a; getsockname(s, (struct sockaddr*)&a, &al);
    dv_web_ls = s; dv_web_port = ntohs(a.sin_port);
    O_Int r = { true, dv_web_port }; return r;
}

// a header's value (case-insensitive name) inside [h, he), or NULL
static const char* dv_web_hdr(const char* h, const char* he, const char* name, size_t* vn) {
    size_t nl = strlen(name);
    for (const char* p = strstr(h, "\r\n"); p && p < he; p = strstr(p + 2, "\r\n")) {
        const char* q = p + 2; size_t i = 0;
        while (i < nl && q + i < he && (q[i] | 32) == (name[i] | 32)) i++;
        if (i == nl && q[i] == ':') {
            q += nl + 1; while (*q == ' ' || *q == '\t') q++;
            const char* e = strstr(q, "\r\n"); if (!e || e > he) e = he;
            *vn = (size_t)(e - q); return q;
        }
    }
    return NULL;
}

// is a whole request in? -1: not yet; -2: bad / too large; otherwise the request's full length
static int64_t dv_web_complete(dv_web_client* c) {
    c->buf[c->len] = 0;
    char* he = strstr(c->buf, "\r\n\r\n");
    if (!he) return c->len >= DV_WEB_MAXHDR ? -2 : -1;
    size_t vn = 0; const char* te = dv_web_hdr(c->buf, he, "Transfer-Encoding", &vn);
    if (te) return -2;                                   // chunked uploads are not supported
    int64_t need = 0; const char* cl = dv_web_hdr(c->buf, he, "Content-Length", &vn);
    if (cl) { need = atoll(cl); if (need < 0 || need > DV_WEB_MAXBODY) return -2; }
    int64_t total = (int64_t)(he + 4 - c->buf) + need;
    return c->len >= total ? total : -1;
}

// [id, method, target, peer, headers ("name: value" lines, names lower case), body]
static L_Text dv_web_take(dv_web_client* c, int64_t total) {
    L_Text r = {0}; char num[24];
    char* he = strstr(c->buf, "\r\n\r\n");
    char* sp1 = memchr(c->buf, ' ', (size_t)(he - c->buf));
    char* sp2 = sp1 ? memchr(sp1 + 1, ' ', (size_t)(he - sp1 - 1)) : NULL;
    char* le = strstr(c->buf, "\r\n");
    if (!sp1 || !sp2 || sp2 > le) { dv_web_plain(c, "400 Bad Request"); return r; }
    c->id = dv_web_nextid++;
    c->deadline = dv_web_now() + 300.0;                 // the program has five minutes to answer
    const char* idt = dv_i64_str(num + 23, c->id);
    L_Text_push(&r, dv_text_new(idt, (int64_t)strlen(idt)));
    L_Text_push(&r, dv_text_new(c->buf, sp1 - c->buf));
    L_Text_push(&r, dv_text_new(sp1 + 1, sp2 - sp1 - 1));
    L_Text_push(&r, dv_text_new(c->peer, (int64_t)strlen(c->peer)));
    dv_buf hb = {0};
    for (const char* p = le + 2; p < he; ) {
        const char* e = strstr(p, "\r\n"); if (!e || e > he) e = he;
        const char* colon = memchr(p, ':', (size_t)(e - p));
        if (colon) {
            for (const char* q = p; q < colon; q++) { char ch = (char)(*q >= 'A' && *q <= 'Z' ? *q + 32 : *q); dv_buf_put(&hb, &ch, 1); }
            dv_buf_puts(&hb, ": ");
            const char* v = colon + 1; while (v < e && (*v == ' ' || *v == '\t')) v++;
            dv_buf_put(&hb, v, (size_t)(e - v)); dv_buf_puts(&hb, "\n");
        }
        p = e + 2;
    }
    L_Text_push(&r, dv_buf_take(&hb));
    L_Text_push(&r, dv_text_new(he + 4, total - (int64_t)(he + 4 - c->buf)));
    return r;
}

// wait up to timeout_ms for one whole request; [] if none came
static L_Text dv_web_next_raw(int64_t timeout_ms) {
    L_Text none = {0};
    if (dv_web_ls == DV_BADSOCK) {
        if (timeout_ms > 0) {
#ifdef _WIN32
            Sleep((DWORD)timeout_ms);
#else
            struct timespec ts = { timeout_ms / 1000, (long)(timeout_ms % 1000) * 1000000L }; nanosleep(&ts, NULL);
#endif
        }
        return none;
    }
    double until = dv_web_now() + (double)timeout_ms / 1000.0;
    for (;;) {
        double now = dv_web_now();
        for (int i = 0; i < DV_WEB_CL; i++) {             // a request already read in full and not handed out yet
            dv_web_client* c = &dv_web_cl[i];
            if (c->s == DV_BADSOCK) continue;
            if (now > c->deadline) { if (c->id) dv_web_plain(c, "503 Service Unavailable"); else dv_web_drop(c); continue; }
            if (c->id) continue;
            int64_t t = dv_web_complete(c);
            if (t == -2) { dv_web_plain(c, "413 Content Too Large"); continue; }
            if (t > 0) { L_Text r = dv_web_take(c, t); if (r.n) return r; }
        }
        fd_set rd; FD_ZERO(&rd); FD_SET(dv_web_ls, &rd);
        int maxfd = (int)dv_web_ls;
        for (int i = 0; i < DV_WEB_CL; i++) {
            dv_web_client* c = &dv_web_cl[i];
            if (c->s != DV_BADSOCK && !c->id) { FD_SET(c->s, &rd); if ((int)c->s > maxfd) maxfd = (int)c->s; }
        }
        double left = until - now; if (left < 0) left = 0;
        struct timeval tv = { (long)left, (long)((left - (long)left) * 1e6) };
        int r = select(maxfd + 1, &rd, NULL, NULL, &tv);
        if (r > 0) {
            if (FD_ISSET(dv_web_ls, &rd)) {
                struct sockaddr_in pa; socklen_t pl = sizeof pa;
                dv_sock s = accept(dv_web_ls, (struct sockaddr*)&pa, &pl);
                if (s != DV_BADSOCK) {
                    int slot = -1;
                    for (int i = 0; i < DV_WEB_CL; i++) if (dv_web_cl[i].s == DV_BADSOCK) { slot = i; break; }
                    if (slot < 0) dv_closesock(s);
                    else {
                        dv_web_client* c = &dv_web_cl[slot];
                        c->s = s; c->cap = 65536; c->buf = dv_alloc((size_t)c->cap + 1); c->len = 0; c->id = 0;
                        c->deadline = now + 30.0;
                        snprintf(c->peer, sizeof c->peer, "%s", inet_ntoa(pa.sin_addr));
                    }
                }
            }
            for (int i = 0; i < DV_WEB_CL; i++) {
                dv_web_client* c = &dv_web_cl[i];
                if (c->s == DV_BADSOCK || c->id || !FD_ISSET(c->s, &rd)) continue;
                if (c->len == c->cap) {
                    if (c->cap >= DV_WEB_MAXHDR + DV_WEB_MAXBODY) { dv_web_plain(c, "413 Content Too Large"); continue; }
                    c->cap *= 2; c->buf = realloc(c->buf, (size_t)c->cap + 1); if (!c->buf) dv_trap("out of memory", "web");
                }
                int k = (int)recv(c->s, c->buf + c->len, (int)(c->cap - c->len), 0);
                if (k <= 0) { dv_web_drop(c); continue; }
                c->len += k;
            }
        }
        if (dv_web_now() >= until && r <= 0) {
            bool ready = false;                           // last look: something may have completed just now
            for (int i = 0; i < DV_WEB_CL && !ready; i++)
                if (dv_web_cl[i].s != DV_BADSOCK && !dv_web_cl[i].id && dv_web_complete(&dv_web_cl[i]) > 0) ready = true;
            if (!ready) return none;
        }
    }
}

// answer request `id`: status line, extra header lines ("Name: value\n"...), body. false if it is gone
static bool dv_web_reply(int64_t id, int64_t status, dv_text reason, dv_text headers, dv_text body) {
    for (int i = 0; i < DV_WEB_CL; i++) {
        dv_web_client* c = &dv_web_cl[i];
        if (c->s == DV_BADSOCK || c->id != id) continue;
        dv_buf h = {0}; char line[160];
        snprintf(line, sizeof line, "HTTP/1.1 %d %.*s\r\n", (int)status, (int)(reason.n > 100 ? 100 : reason.n), reason.p);
        dv_buf_puts(&h, line);
        for (int64_t k = 0; k < headers.n; k++) {
            if (headers.p[k] == '\r') continue;
            if (headers.p[k] == '\n') dv_buf_puts(&h, "\r\n"); else dv_buf_put(&h, &headers.p[k], 1);
        }
        if (headers.n && headers.p[headers.n - 1] != '\n') dv_buf_puts(&h, "\r\n");
        snprintf(line, sizeof line, "Content-Length: %lld\r\nConnection: close\r\n\r\n", (long long)body.n);
        dv_buf_puts(&h, line);
        dv_web_sendall(c->s, h.p, (size_t)h.n); free(h.p);
        if (body.n) dv_web_sendall(c->s, body.p, (size_t)body.n);
#ifdef _WIN32
        shutdown(c->s, SD_SEND);
#else
        shutdown(c->s, SHUT_WR);
#endif
        dv_web_drop(c);
        return true;
    }
    return false;
}
