// native side of module `proc`. Arguments are borrowed; results are owned by the caller.
#ifndef _WIN32
#include <unistd.h>
#include <poll.h>
#include <signal.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/wait.h>
#endif

static double dv_proc_now(void) {
#ifdef _WIN32
    return (double)GetTickCount64();
#else
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
#endif
}

static char* dv_proc_cstr(dv_text t) { char* c = dv_alloc((size_t)t.n + 1); if (t.n) memcpy(c, t.p, (size_t)t.n); c[t.n] = 0; return c; }

static L_Text dv_proc_result(int64_t code, dv_buf* out, dv_buf* err, bool timed_out) {
    L_Text r = {0}; char s[24];
    L_Text_push(&r, dv_text_new(dv_i64_str(s + 23, code), (int64_t)strlen(dv_i64_str(s + 23, code))));
    L_Text_push(&r, dv_buf_take(out));
    L_Text_push(&r, dv_buf_take(err));
    L_Text_push(&r, dv_text_new(timed_out ? "1" : "0", 1));
    return r;
}

#ifdef _WIN32
// one argument quoted the way the C runtime of the called program splits it back
static void dv_proc_quote(dv_buf* b, dv_text a) {
    bool need = a.n == 0;
    for (int64_t i = 0; i < a.n; i++) if (a.p[i] == ' ' || a.p[i] == '\t' || a.p[i] == '"' || a.p[i] == '\n') need = true;
    if (!need) { dv_buf_put(b, a.p, (size_t)a.n); return; }
    dv_buf_puts(b, "\"");
    int64_t slashes = 0;
    for (int64_t i = 0; i < a.n; i++) {
        char c = a.p[i];
        if (c == '\\') { slashes++; continue; }
        if (c == '"') { for (int64_t k = 0; k < slashes * 2 + 1; k++) dv_buf_puts(b, "\\"); dv_buf_put(b, "\"", 1); }
        else { for (int64_t k = 0; k < slashes; k++) dv_buf_puts(b, "\\"); dv_buf_put(b, &c, 1); }
        slashes = 0;
    }
    for (int64_t k = 0; k < slashes * 2; k++) dv_buf_puts(b, "\\");
    dv_buf_puts(b, "\"");
}
typedef struct { HANDLE h; dv_buf buf; } dv_proc_reader;
static DWORD WINAPI dv_proc_read_thread(LPVOID p) {
    dv_proc_reader* r = p; char chunk[65536]; DWORD n;
    while (ReadFile(r->h, chunk, sizeof chunk, &n, NULL) && n > 0) dv_buf_put(&r->buf, chunk, n);
    return 0;
}
typedef struct { HANDLE h; dv_text t; } dv_proc_writer;
static DWORD WINAPI dv_proc_write_thread(LPVOID p) {
    dv_proc_writer* w = p; int64_t off = 0; DWORD n;
    while (off < w->t.n && WriteFile(w->h, w->t.p + off, (DWORD)(w->t.n - off > 65536 ? 65536 : w->t.n - off), &n, NULL)) off += n;
    CloseHandle(w->h); return 0;
}
static L_Text dv_proc_run_raw(L_Text argv, dv_text input, int64_t timeout_ms) {
    dv_buf out = {0}, err = {0}, cmd = {0};
    for (int64_t i = 0; i < argv.n; i++) { if (i) dv_buf_puts(&cmd, " "); dv_proc_quote(&cmd, argv.p[i]); }
    dv_text cl = dv_buf_take(&cmd);
    wchar_t* wcl = dv_wide(cl.p, cl.n); dv_text_free(cl);
    SECURITY_ATTRIBUTES sa = { sizeof sa, NULL, TRUE };
    HANDLE in_r, in_w, out_r, out_w, err_r, err_w;
    CreatePipe(&in_r, &in_w, &sa, 0); CreatePipe(&out_r, &out_w, &sa, 0); CreatePipe(&err_r, &err_w, &sa, 0);
    SetHandleInformation(in_w, HANDLE_FLAG_INHERIT, 0); SetHandleInformation(out_r, HANDLE_FLAG_INHERIT, 0); SetHandleInformation(err_r, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW si; memset(&si, 0, sizeof si); si.cb = sizeof si; si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in_r; si.hStdOutput = out_w; si.hStdError = err_w;
    PROCESS_INFORMATION pi;
    BOOL ok = CreateProcessW(NULL, wcl, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
    free(wcl); CloseHandle(in_r); CloseHandle(out_w); CloseHandle(err_w);
    if (!ok) {
        CloseHandle(in_w); CloseHandle(out_r); CloseHandle(err_r);
        dv_buf_puts(&err, "cannot start the program (not found or not runnable)");
        return dv_proc_result(127, &out, &err, false);
    }
    dv_proc_writer w = { in_w, input };
    dv_proc_reader ro = { out_r, {0} }, re = { err_r, {0} };
    HANDLE th[3];
    th[0] = CreateThread(NULL, 0, dv_proc_write_thread, &w, 0, NULL);
    th[1] = CreateThread(NULL, 0, dv_proc_read_thread, &ro, 0, NULL);
    th[2] = CreateThread(NULL, 0, dv_proc_read_thread, &re, 0, NULL);
    bool timed_out = WaitForSingleObject(pi.hProcess, timeout_ms > 0 ? (DWORD)timeout_ms : INFINITE) == WAIT_TIMEOUT;
    if (timed_out) { TerminateProcess(pi.hProcess, 1); WaitForSingleObject(pi.hProcess, INFINITE); }
    WaitForMultipleObjects(3, th, TRUE, 5000);
    DWORD code = 0; GetExitCodeProcess(pi.hProcess, &code);
    for (int k = 0; k < 3; k++) CloseHandle(th[k]);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread); CloseHandle(out_r); CloseHandle(err_r);
    out = ro.buf; err = re.buf;
    return dv_proc_result(timed_out ? -1 : (int64_t)code, &out, &err, timed_out);
}
static void dv_proc_sleep(int64_t ms) { Sleep((DWORD)ms); }
static O_Text dv_proc_env(dv_text name) {
    O_Text r = {0}; wchar_t* w = dv_wide(name.p, name.n);
    DWORD n = GetEnvironmentVariableW(w, NULL, 0);
    if (n > 0) { wchar_t* v = malloc(sizeof(wchar_t) * n); GetEnvironmentVariableW(w, v, n); char* s = dv_narrow(v); r.has = true; r.v = dv_text_new(s, (int64_t)strlen(s)); free(s); free(v); }
    free(w); return r;
}
#else
static L_Text dv_proc_run_raw(L_Text argv, dv_text input, int64_t timeout_ms) {
    dv_buf out = {0}, err = {0};
    int pin[2], pout[2], perr[2], pexec[2];
    if (pipe(pin) || pipe(pout) || pipe(perr) || pipe(pexec)) { dv_buf_puts(&err, "cannot make pipes"); return dv_proc_result(127, &out, &err, false); }
    fcntl(pexec[1], F_SETFD, FD_CLOEXEC);
    char** av = dv_alloc(sizeof(char*) * (size_t)(argv.n + 1));
    for (int64_t i = 0; i < argv.n; i++) av[i] = dv_proc_cstr(argv.p[i]);
    av[argv.n] = NULL;
    fflush(stdout); fflush(stderr);
    pid_t pid = fork();
    if (pid == 0) {
        dup2(pin[0], 0); dup2(pout[1], 1); dup2(perr[1], 2);
        close(pin[0]); close(pin[1]); close(pout[0]); close(pout[1]); close(perr[0]); close(perr[1]); close(pexec[0]);
        execvp(av[0], av);
        int e = errno; ssize_t wr = write(pexec[1], &e, sizeof e); (void)wr;
        _exit(127);
    }
    for (int64_t i = 0; i < argv.n; i++) free(av[i]);
    free(av);
    close(pin[0]); close(pout[1]); close(perr[1]); close(pexec[1]);
    if (pid < 0) {
        close(pin[1]); close(pout[0]); close(perr[0]); close(pexec[0]);
        dv_buf_puts(&err, "cannot start a process"); return dv_proc_result(127, &out, &err, false);
    }
    int e = 0;
    if (read(pexec[0], &e, sizeof e) == (ssize_t)sizeof e) {    // exec failed: the child reported errno
        close(pexec[0]); close(pin[1]); close(pout[0]); close(perr[0]); waitpid(pid, NULL, 0);
        dv_buf_puts(&err, "cannot start the program: "); dv_buf_puts(&err, strerror(e));
        return dv_proc_result(127, &out, &err, false);
    }
    close(pexec[0]);
    void (*old)(int) = signal(SIGPIPE, SIG_IGN);
    fcntl(pin[1], F_SETFL, O_NONBLOCK);
    int64_t off = 0; int in_fd = pin[1];
    if (input.n == 0) { close(in_fd); in_fd = -1; }
    bool open_out = true, open_err = true, timed_out = false;
    double start = dv_proc_now();
    char chunk[65536];
    while (open_out || open_err) {
        struct pollfd fds[3]; int nf = 0, io = -1, ie = -1, ii = -1;
        if (open_out) { fds[nf].fd = pout[0]; fds[nf].events = POLLIN; io = nf++; }
        if (open_err) { fds[nf].fd = perr[0]; fds[nf].events = POLLIN; ie = nf++; }
        if (in_fd >= 0) { fds[nf].fd = in_fd; fds[nf].events = POLLOUT; ii = nf++; }
        int wait = 200;
        if (timeout_ms > 0) {
            double left = timeout_ms - (dv_proc_now() - start);
            if (left <= 0) { timed_out = true; kill(pid, SIGKILL); break; }
            if (left < wait) wait = (int)left + 1;
        }
        int r = poll(fds, (nfds_t)nf, wait);
        if (r < 0 && errno != EINTR) break;
        if (r <= 0) continue;
        if (io >= 0 && fds[io].revents) { ssize_t n = read(pout[0], chunk, sizeof chunk); if (n > 0) dv_buf_put(&out, chunk, (size_t)n); else open_out = false; }
        if (ie >= 0 && fds[ie].revents) { ssize_t n = read(perr[0], chunk, sizeof chunk); if (n > 0) dv_buf_put(&err, chunk, (size_t)n); else open_err = false; }
        if (ii >= 0 && fds[ii].revents) {
            ssize_t n = write(in_fd, input.p + off, (size_t)(input.n - off > 65536 ? 65536 : input.n - off));
            if (n > 0) off += n;
            if (n < 0 && errno != EAGAIN) off = input.n;
            if (off >= input.n) { close(in_fd); in_fd = -1; }
        }
    }
    if (in_fd >= 0) close(in_fd);
    close(pout[0]); close(perr[0]);
    int st = 0;
    while (!timed_out && timeout_ms > 0 && waitpid(pid, &st, WNOHANG) == 0) {   // output closed, process still running
        if (dv_proc_now() - start >= timeout_ms) { timed_out = true; kill(pid, SIGKILL); break; }
        struct timespec ts = { 0, 10000000L }; nanosleep(&ts, NULL);
    }
    if (timed_out || timeout_ms <= 0) waitpid(pid, &st, 0);
    signal(SIGPIPE, old);
    int64_t code = timed_out ? -1 : (WIFEXITED(st) ? WEXITSTATUS(st) : 128 + (WIFSIGNALED(st) ? WTERMSIG(st) : 0));
    return dv_proc_result(code, &out, &err, timed_out);
}
static void dv_proc_sleep(int64_t ms) { struct timespec ts = { ms / 1000, (long)(ms % 1000) * 1000000L }; nanosleep(&ts, NULL); }
static O_Text dv_proc_env(dv_text name) {
    O_Text r = {0}; char* c = dv_proc_cstr(name); const char* v = getenv(c); free(c);
    if (v) { r.has = true; r.v = dv_text_new(v, (int64_t)strlen(v)); }
    return r;
}
#endif
static void dv_proc_exit(int64_t code) { fflush(stdout); fflush(stderr); exit((int)code); }
