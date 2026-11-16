// Module `ui`, native side: a tiny single-threaded HTTP server inside the program plus one fixed page.
// The page long-polls GET /view?v=N for the interface tree (JSON made by the Dovod side) and POSTs what the
// user does to /event. Nothing runs between calls: sockets are served only inside ui_event / ui_push,
// so the program owns the loop. State kept here: the last tree, a queue of events, open connections.
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

/*PAGE-BEGIN*/
static const char dv_ui_page[] =
    "<!doctype html>\n"
    "<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>\n"
    "<title>Dovod</title>\n"
    "<style>\n"
    ":root{--bg:#f6f7f9;--fg:#1c1f24;--mut:#667085;--card:#fff;--line:#d9dde3;--acc:#2563eb;--accfg:#fff}\n"
    "@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e7e9ec;--mut:#98a2b3;--card:#1e2126;--line:#353a42;--acc:#4f8cff;--accfg:#fff}}\n"
    "*{box-sizing:border-box}\n"
    "body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,'Segoe UI',Roboto,sans-serif}\n"
    "#app{max-width:1100px;margin:0 auto;padding:16px}\n"
    ".col{display:flex;flex-direction:column;gap:10px}\n"
    ".row{display:flex;flex-direction:row;gap:10px;align-items:center;flex-wrap:wrap}\n"
    "h2{margin:6px 0 2px;font-size:20px}\n"
    "p{margin:0;white-space:pre-wrap}\n"
    "button{font:inherit;padding:6px 14px;border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}\n"
    "button:hover{border-color:var(--acc)}\n"
    "button:active{background:var(--acc);color:var(--accfg)}\n"
    "input[type=text],select{font:inherit;padding:6px 9px;border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--fg);min-width:120px}\n"
    "input[type=text]{flex:1}\n"
    "input:focus,select:focus,button:focus-visible{outline:2px solid var(--acc);outline-offset:1px}\n"
    "label{display:inline-flex;gap:6px;align-items:center;cursor:pointer}\n"
    "fieldset{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:12px;margin:0;min-width:0}\n"
    "legend{padding:0 6px;color:var(--mut);font-size:13px}\n"
    "table{border-collapse:collapse;width:100%}\n"
    "th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:middle}\n"
    "th{color:var(--mut);font-weight:600;font-size:13px}\n"
    "progress{width:100%;height:14px;accent-color:var(--acc)}\n"
    "input[type=range]{flex:1;min-width:140px;accent-color:var(--acc)}\n"
    ".sv{min-width:3ch;text-align:right;color:var(--mut)}\n"
    ".row>.row{flex:1;flex-wrap:nowrap}\n"
    "#off{position:fixed;left:0;right:0;bottom:0;padding:8px;text-align:center;background:#b42318;color:#fff;display:none}\n"
    "</style></head><body><div id='app'></div><div id='off'>The program is not answering.</div>\n"
    "<script>\n"
    "var ver = 0, sent = {}, chain = Promise.resolve(), drag = false, pending = null;\n"
    "function send(s) { chain = chain.then(function () { return fetch('/event', {method: 'POST', body: s}); }).catch(function () {}); }\n"
    "function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }\n"
    "function key(n) { return n.id + '\\n' + n.arg; }\n"
    "function change(n, v) { sent[key(n)] = v; send('change:' + key(n) + '\\n' + v); }\n"
    "function kids(parent, list, keep) { for (var i = 0; i < list.length; i++) parent.appendChild(build(list[i], keep)); return parent; }\n"
    "function build(n, keep) {\n"
    "  var e, i;\n"
    "  switch (n.t) {\n"
    "    case 'text': return el('p', '', n.text);\n"
    "    case 'heading': return el('h2', '', n.text);\n"
    "    case 'button': e = el('button', '', n.label); e.dataset.id = key(n); e.onclick = function () { send('click:' + key(n)); }; return e;\n"
    "    case 'input':\n"
    "      e = el('input'); e.type = 'text'; e.dataset.id = key(n); e.placeholder = n.hint;\n"
    "      e.value = (keep && keep.id === key(n) && n.value === sent[key(n)]) ? keep.value : n.value;\n"
    "      e.oninput = function () { change(n, e.value); }; return e;\n"
    "    case 'check':\n"
    "      e = el('label'); var c = el('input'); c.type = 'checkbox'; c.checked = n.on; c.dataset.id = key(n);\n"
    "      c.onchange = function () { change(n, c.checked ? 'true' : 'false'); };\n"
    "      e.appendChild(c); e.appendChild(document.createTextNode(n.label)); return e;\n"
    "    case 'slider':\n"
    "      e = el('div', 'row'); var r = el('input'); r.type = 'range'; r.min = n.lo; r.max = n.hi; r.value = n.value; r.dataset.id = key(n);\n"
    "      var sv = el('span', 'sv', String(n.value));\n"
    "      r.onpointerdown = function () { drag = true; };\n"
    "      r.oninput = function () { sv.textContent = r.value; change(n, r.value); };\n"
    "      e.appendChild(r); e.appendChild(sv); return e;\n"
    "    case 'select':\n"
    "      e = el('select'); e.dataset.id = key(n);\n"
    "      for (i = 0; i < n.options.length; i++) { var o = el('option', '', n.options[i]); o.value = n.options[i]; e.appendChild(o); }\n"
    "      e.value = n.value; e.onchange = function () { change(n, e.value); }; return e;\n"
    "    case 'progress': e = el('progress'); e.max = n.total; e.value = n.value; return e;\n"
    "    case 'row': return kids(el('div', 'row'), n.kids, keep);\n"
    "    case 'col': return kids(el('div', 'col'), n.kids, keep);\n"
    "    case 'box': e = el('fieldset', 'col'); if (n.title) e.appendChild(el('legend', '', n.title)); return kids(e, n.kids, keep);\n"
    "    case 'table':\n"
    "      e = el('table'); var tr = el('tr');\n"
    "      for (i = 0; i < n.head.length; i++) tr.appendChild(el('th', '', n.head[i]));\n"
    "      e.appendChild(tr);\n"
    "      for (i = 0; i < n.rows.length; i++) {\n"
    "        tr = el('tr'); var cells = n.rows[i].kids || [n.rows[i]];\n"
    "        for (var j = 0; j < cells.length; j++) { var td = el('td'); td.appendChild(build(cells[j], keep)); tr.appendChild(td); }\n"
    "        e.appendChild(tr);\n"
    "      }\n"
    "      return e;\n"
    "    case 'html': e = el('div'); e.innerHTML = n.html; return e;\n"
    "  }\n"
    "  return el('p', '', '[unknown: ' + n.t + ']');\n"
    "}\n"
    "function render(tree) {\n"
    "  if (drag) { pending = tree; return; }   // do not pull a slider out from under the finger\n"
    "  var a = document.activeElement, keep = null, app = document.getElementById('app');\n"
    "  if (a && a.dataset && a.dataset.id) keep = {id: a.dataset.id, value: a.value, s: a.selectionStart, e: a.selectionEnd};\n"
    "  var fresh = build(tree, keep);\n"
    "  app.textContent = ''; app.appendChild(fresh);\n"
    "  if (keep) {\n"
    "    var all = app.querySelectorAll('[data-id]');\n"
    "    for (var i = 0; i < all.length; i++) if (all[i].dataset.id === keep.id) {\n"
    "      all[i].focus();\n"
    "      if (all[i].type === 'text' && keep.s !== null) { try { all[i].setSelectionRange(keep.s, keep.e); } catch (x) {} }\n"
    "      break;\n"
    "    }\n"
    "  }\n"
    "}\n"
    "function poll() {\n"
    "  fetch('/view?v=' + ver).then(function (r) { return r.json(); }).then(function (j) {\n"
    "    document.getElementById('off').style.display = 'none';\n"
    "    if (j.title) document.title = j.title;\n"
    "    if (j.tree) { ver = j.v; render(j.tree); }\n"
    "    poll();\n"
    "  }).catch(function () { document.getElementById('off').style.display = 'block'; setTimeout(poll, 700); });\n"
    "}\n"
    "window.addEventListener('pointerup', function () { if (drag) { drag = false; if (pending) { var t = pending; pending = null; render(t); } } });\n"
    "poll();\n"
    "</script></body></html>\n";
/*PAGE-END*/

#define DV_UI_CL 32
#define DV_UI_BUF 65536
#define DV_UI_Q 256
typedef struct { dv_sock s; char* buf; int len; int waiting; long long since; double deadline; } dv_ui_client;
static dv_sock dv_ui_ls = DV_BADSOCK;
static dv_ui_client dv_ui_cl[DV_UI_CL];
static int dv_ui_port = 0, dv_ui_opened = 0;
static char* dv_ui_tree = NULL; static size_t dv_ui_tree_n = 0; static long long dv_ui_ver = 0;
static char* dv_ui_title = NULL;
static dv_text dv_ui_q[DV_UI_Q]; static int dv_ui_qh = 0, dv_ui_qn = 0;
static double dv_ui_last_poll = 0;

static double dv_ui_now(void) {
#ifdef _WIN32
    return (double)GetTickCount64() / 1000.0;
#else
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
#endif
}
static void dv_ui_qpush(const char* p, int64_t n) {
    if (dv_ui_qn == DV_UI_Q) return;   // the program is not reading: drop the newest
    dv_ui_q[(dv_ui_qh + dv_ui_qn++) % DV_UI_Q] = dv_text_new(p, n);
}
static void dv_ui_sendall(dv_sock s, const char* p, size_t n) {
    while (n > 0) { int k = (int)send(s, p, (int)(n > 1 << 20 ? 1 << 20 : n), DV_NOSIG); if (k <= 0) return; p += k; n -= (size_t)k; }
}
static void dv_ui_reply(dv_sock s, const char* status, const char* ctype, const char* body, size_t n) {
    char h[256];
    int k = snprintf(h, sizeof h, "HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %lu\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
                     status, ctype, (unsigned long)n);
    dv_ui_sendall(s, h, (size_t)k);
    if (n) dv_ui_sendall(s, body, n);
}
static void dv_ui_drop(dv_ui_client* c) { dv_closesock(c->s); free(c->buf); c->s = DV_BADSOCK; c->buf = NULL; c->len = 0; c->waiting = 0; }
// {"v":N,"title":"...","tree":...}; without the tree when there is nothing newer than the page has
static void dv_ui_send_view(dv_ui_client* c, int with_tree) {
    dv_buf b = {0}; char num[40];
    snprintf(num, sizeof num, "{\"v\":%lld,\"title\":\"", dv_ui_ver); dv_buf_puts(&b, num);
    for (const char* t = dv_ui_title ? dv_ui_title : "Dovod"; *t; t++) {
        unsigned char ch = (unsigned char)*t; char one[8];
        if (ch == '"' || ch == '\\') { one[0] = '\\'; one[1] = (char)ch; one[2] = 0; }
        else if (ch < 32) snprintf(one, sizeof one, "\\u%04x", ch);
        else { one[0] = (char)ch; one[1] = 0; }
        dv_buf_puts(&b, one);
    }
    dv_buf_puts(&b, "\"");
    size_t n = (size_t)b.n; char* out = b.p;
    if (with_tree && dv_ui_tree) {
        out = malloc(n + 8 + dv_ui_tree_n + 2);
        if (!out) dv_trap("out of memory", "ui");
        memcpy(out, b.p, n); memcpy(out + n, ",\"tree\":", 8); memcpy(out + n + 8, dv_ui_tree, dv_ui_tree_n);
        n += 8 + dv_ui_tree_n; out[n++] = '}';
        free(b.p);
    } else { dv_buf_puts(&b, "}"); out = b.p; n = (size_t)b.n; }
    dv_ui_reply(c->s, "200 OK", "application/json; charset=utf-8", out, n);
    free(out);
    dv_ui_drop(c);
}
static void dv_ui_handle(dv_ui_client* c) {   // a complete request is in c->buf
    char* hdr_end = strstr(c->buf, "\r\n\r\n");
    char* body = hdr_end + 4; int body_n = c->len - (int)(body - c->buf);
    if (!strncmp(c->buf, "GET /view", 9)) {
        long long v = 0; const char* q = strstr(c->buf, "v="); if (q && q < hdr_end) v = atoll(q + 2);
        dv_ui_last_poll = dv_ui_now();
        if (!dv_ui_opened) { dv_ui_opened = 1; dv_ui_qpush("opened", 6); }
        if (dv_ui_tree && dv_ui_ver > v) { dv_ui_send_view(c, 1); return; }
        c->waiting = 1; c->since = v; c->deadline = dv_ui_now() + 2.0; return;
    }
    if (!strncmp(c->buf, "POST /event", 11)) {
        dv_ui_last_poll = dv_ui_now();
        if (body_n > 0) dv_ui_qpush(body, body_n);
        dv_ui_reply(c->s, "204 No Content", "text/plain", "", 0); dv_ui_drop(c); return;
    }
    if (!strncmp(c->buf, "GET / ", 6)) {
        dv_ui_reply(c->s, "200 OK", "text/html; charset=utf-8", dv_ui_page, sizeof dv_ui_page - 1); dv_ui_drop(c); return;
    }
    dv_ui_reply(c->s, "404 Not Found", "text/plain", "not found", 9); dv_ui_drop(c);
}
static void dv_ui_pump(int timeout_ms) {
    if (dv_ui_ls == DV_BADSOCK) {   // not listening: just wait, so a loop around ui_event does not spin
        if (timeout_ms > 0) {
#ifdef _WIN32
            Sleep((DWORD)timeout_ms);
#else
            struct timeval w = { 0, timeout_ms * 1000 }; select(0, NULL, NULL, NULL, &w);
#endif
        }
        return;
    }
    fd_set rd; FD_ZERO(&rd); FD_SET(dv_ui_ls, &rd);
    int maxfd = (int)dv_ui_ls;
    for (int i = 0; i < DV_UI_CL; i++) if (dv_ui_cl[i].s != DV_BADSOCK) { FD_SET(dv_ui_cl[i].s, &rd); if ((int)dv_ui_cl[i].s > maxfd) maxfd = (int)dv_ui_cl[i].s; }
    struct timeval tv = { timeout_ms / 1000, (timeout_ms % 1000) * 1000 };
    int r = select(maxfd + 1, &rd, NULL, NULL, &tv);
    double now = dv_ui_now();
    if (r > 0) {
        for (int i = 0; i < DV_UI_CL; i++) {
            dv_ui_client* c = &dv_ui_cl[i];
            if (c->s == DV_BADSOCK || !FD_ISSET(c->s, &rd)) continue;
            int k = (int)recv(c->s, c->buf + c->len, DV_UI_BUF - 1 - c->len, 0);
            if (k <= 0) { dv_ui_drop(c); continue; }      // closed by the browser (also ends a waiting poll)
            if (c->waiting) continue;
            c->len += k; c->buf[c->len] = 0;
            char* he = strstr(c->buf, "\r\n\r\n");
            if (!he) { if (c->len >= DV_UI_BUF - 1) dv_ui_drop(c); continue; }
            long need = 0; const char* cl = strstr(c->buf, "Content-Length:");
            if (!cl || cl > he) cl = strstr(c->buf, "content-length:");
            if (cl && cl < he) need = atol(cl + 15);
            if (need > DV_UI_BUF - 2048) { dv_ui_drop(c); continue; }
            if (c->len - (int)(he + 4 - c->buf) < need) continue;   // the body is still coming
            dv_ui_handle(c);
        }
        if (FD_ISSET(dv_ui_ls, &rd)) {
            dv_sock s = accept(dv_ui_ls, NULL, NULL);
            if (s != DV_BADSOCK) {
                int slot = -1;
                for (int i = 0; i < DV_UI_CL; i++) if (dv_ui_cl[i].s == DV_BADSOCK) { slot = i; break; }
                if (slot < 0) dv_closesock(s);
                else {
                    dv_ui_client* c = &dv_ui_cl[slot];
                    c->s = s; c->buf = dv_alloc(DV_UI_BUF); c->len = 0; c->waiting = 0; c->deadline = now + 10.0;
                }
            }
        }
    }
    int waiting = 0;
    for (int i = 0; i < DV_UI_CL; i++) {
        dv_ui_client* c = &dv_ui_cl[i];
        if (c->s == DV_BADSOCK) continue;
        if (c->waiting) {
            if (dv_ui_tree && dv_ui_ver > c->since) dv_ui_send_view(c, 1);
            else if (now > c->deadline) { dv_ui_last_poll = now; dv_ui_send_view(c, 0); }
            else waiting++;
        } else if (now > c->deadline) dv_ui_drop(c);   // a connection that never sent a full request
    }
    if (dv_ui_opened && !waiting && now - dv_ui_last_poll > 5.0) { dv_ui_opened = 0; dv_ui_qpush("closed", 6); }
}

static O_Int dv_ui_listen(int64_t port, bool lan) {
    O_Int none = {0};
    if (dv_ui_ls != DV_BADSOCK) { O_Int r = { true, dv_ui_port }; return r; }
#ifdef _WIN32
    WSADATA w; if (WSAStartup(MAKEWORD(2, 2), &w) != 0) return none;
#endif
    for (int i = 0; i < DV_UI_CL; i++) dv_ui_cl[i].s = DV_BADSOCK;
    dv_sock s = socket(AF_INET, SOCK_STREAM, 0);
    if (s == DV_BADSOCK) return none;
    int one = 1; setsockopt(s, SOL_SOCKET, SO_REUSEADDR, (const char*)&one, sizeof one);
    struct sockaddr_in a; memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = htonl(lan ? INADDR_ANY : INADDR_LOOPBACK);
    if (bind(s, (struct sockaddr*)&a, sizeof a) != 0 || listen(s, 16) != 0) { dv_closesock(s); return none; }
    socklen_t al = sizeof a; getsockname(s, (struct sockaddr*)&a, &al);
    dv_ui_ls = s; dv_ui_port = ntohs(a.sin_port);
    O_Int r = { true, dv_ui_port }; return r;
}
// open the user's browser on the page: as an app window when a Chromium-family browser is there, else a tab.
// DOVOD_UI_NOWINDOW=1 skips this (tests, servers).
static void dv_ui_window(dv_text title) {
    free(dv_ui_title); dv_ui_title = dv_alloc((size_t)title.n + 1); memcpy(dv_ui_title, title.p, (size_t)title.n); dv_ui_title[title.n] = 0;
    dv_ui_ver++;   // pages pick the new title up
    const char* no = getenv("DOVOD_UI_NOWINDOW");
    if (dv_ui_ls == DV_BADSOCK || (no && *no && *no != '0')) return;
    char url[64]; snprintf(url, sizeof url, "http://127.0.0.1:%d/", dv_ui_port);
#ifdef _WIN32
    char arg[96]; snprintf(arg, sizeof arg, "--app=%s", url);
    if ((INT_PTR)ShellExecuteA(NULL, "open", "msedge", arg, NULL, SW_SHOWNORMAL) <= 32 &&
        (INT_PTR)ShellExecuteA(NULL, "open", "chrome", arg, NULL, SW_SHOWNORMAL) <= 32)
        ShellExecuteA(NULL, "open", url, NULL, NULL, SW_SHOWNORMAL);
#else
    char cmd[700];
    snprintf(cmd, sizeof cmd,
             "(for b in chromium chromium-browser google-chrome microsoft-edge brave-browser; do "
             "if command -v $b >/dev/null 2>&1; then exec $b --app=%s; fi; done; "
             "if command -v xdg-open >/dev/null 2>&1; then exec xdg-open %s; fi; "
             "if command -v open >/dev/null 2>&1; then exec open %s; fi) >/dev/null 2>&1 &", url, url, url);
    if (system(cmd) != 0) { /* no browser found: the page is still served at the URL */ }
#endif
}
static void dv_ui_push(dv_text json) {
    if (!(dv_ui_tree && dv_ui_tree_n == (size_t)json.n && !memcmp(dv_ui_tree, json.p, dv_ui_tree_n))) {   // same tree: no redraw
        free(dv_ui_tree); dv_ui_tree = dv_alloc((size_t)json.n + 1); memcpy(dv_ui_tree, json.p, (size_t)json.n);
        dv_ui_tree_n = (size_t)json.n; dv_ui_ver++;
    }
    dv_ui_pump(0);
}
static dv_text dv_ui_event(void) {
    if (!dv_ui_qn) dv_ui_pump(100); else dv_ui_pump(0);
    if (!dv_ui_qn) return dv_text_new("tick", 4);
    dv_text e = dv_ui_q[dv_ui_qh]; dv_ui_qh = (dv_ui_qh + 1) % DV_UI_Q; dv_ui_qn--;
    return e;
}
