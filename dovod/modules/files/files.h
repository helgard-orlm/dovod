// native side of module `files`. Arguments are borrowed; results are owned by the caller.
// Paths are UTF-8 Text; on Windows they are converted to UTF-16 (dv_wide), both '/' and '\' work there.
#ifdef _WIN32
#include <direct.h>
#else
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <errno.h>
#endif

// a NUL-terminated copy, or NULL if the path holds a NUL byte (it would silently name another file)
static char* dv_files_cpath(dv_text p) {
    if (p.n == 0 || memchr(p.p, 0, (size_t)p.n)) return NULL;
    char* c = dv_alloc((size_t)p.n + 1); memcpy(c, p.p, (size_t)p.n); c[p.n] = 0; return c;
}

#ifdef _WIN32
static wchar_t* dv_files_wpath(dv_text p) {
    if (p.n == 0 || memchr(p.p, 0, (size_t)p.n)) return NULL;
    return dv_wide(p.p, p.n);
}
// size, mtime (ms since 1970), is-dir; false if it does not exist
static bool dv_files_stat(dv_text p, int64_t* size, int64_t* mtime, bool* dir) {
    wchar_t* w = dv_files_wpath(p); if (!w) return false;
    WIN32_FILE_ATTRIBUTE_DATA a; BOOL ok = GetFileAttributesExW(w, GetFileExInfoStandard, &a); free(w);
    if (!ok) return false;
    *size = ((int64_t)a.nFileSizeHigh << 32) | a.nFileSizeLow;
    int64_t ft = ((int64_t)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime;
    *mtime = (ft - 116444736000000000LL) / 10000;
    *dir = (a.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    return true;
}
#else
static bool dv_files_stat(dv_text p, int64_t* size, int64_t* mtime, bool* dir) {
    char* c = dv_files_cpath(p); if (!c) return false;
    struct stat st; int r = stat(c, &st); free(c);
    if (r != 0) return false;
    *size = (int64_t)st.st_size;
#if defined(__APPLE__)
    *mtime = (int64_t)st.st_mtimespec.tv_sec * 1000 + st.st_mtimespec.tv_nsec / 1000000;
#else
    *mtime = (int64_t)st.st_mtim.tv_sec * 1000 + st.st_mtim.tv_nsec / 1000000;
#endif
    *dir = S_ISDIR(st.st_mode);
    return true;
}
#endif

static bool dv_files_exists(dv_text p) { int64_t s, m; bool d; return dv_files_stat(p, &s, &m, &d); }
static bool dv_files_is_dir(dv_text p) { int64_t s, m; bool d = false; return dv_files_stat(p, &s, &m, &d) && d; }
static O_Int dv_files_size(dv_text p) { O_Int r = {0}; int64_t s, m; bool d; if (dv_files_stat(p, &s, &m, &d) && !d) { r.has = true; r.v = s; } return r; }
static O_Int dv_files_mtime(dv_text p) { O_Int r = {0}; int64_t s, m; bool d; if (dv_files_stat(p, &s, &m, &d)) { r.has = true; r.v = m; } return r; }

// ["1", name, name, ...] sorted by bytes, without "." and ".."; ["0"] if the folder cannot be read
static L_Text dv_files_list_raw(dv_text p) {
    L_Text names = {0}, r = {0};
#ifdef _WIN32
    dv_text pat = dv_text_concat(p, DV_LIT("/*", 2));
    wchar_t* w = dv_files_wpath(pat); dv_text_free(pat);
    WIN32_FIND_DATAW fd; HANDLE h = w ? FindFirstFileW(w, &fd) : INVALID_HANDLE_VALUE; free(w);
    if (h == INVALID_HANDLE_VALUE) { L_Text_push(&r, dv_text_new("0", 1)); return r; }
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
        char* n = dv_narrow(fd.cFileName); L_Text_push(&names, dv_text_new(n, (int64_t)strlen(n))); free(n);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
#else
    char* c = dv_files_cpath(p);
    DIR* d = c ? opendir(c) : NULL; free(c);
    if (!d) { L_Text_push(&r, dv_text_new("0", 1)); return r; }
    struct dirent* e;
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) continue;
        L_Text_push(&names, dv_text_new(e->d_name, (int64_t)strlen(e->d_name)));
    }
    closedir(d);
#endif
    int64_t* o = dv_key_order(names.p, names.n, dv_kcmp_text);
    L_Text_push(&r, dv_text_new("1", 1));
    for (int64_t i = 0; i < names.n; i++) L_Text_push(&r, dv_text_copy(names.p[o[i]]));
    free(o); L_Text_free(names);
    return r;
}

// one folder; true if it exists afterwards
static bool dv_files_mkdir1(dv_text p) {
#ifdef _WIN32
    wchar_t* w = dv_files_wpath(p); if (!w) return false;
    CreateDirectoryW(w, NULL); free(w);
#else
    char* c = dv_files_cpath(p); if (!c) return false;
    mkdir(c, 0777); free(c);
#endif
    return dv_files_is_dir(p);
}

// the folder and its missing parents
static bool dv_files_mkdir(dv_text p) {
    if (dv_files_is_dir(p)) return true;
    for (int64_t i = 1; i < p.n; i++) {
        if (p.p[i] == '/' || p.p[i] == '\\') {
            if (i > 0 && p.p[i - 1] == ':') continue;           // "C:/" — a drive, not a folder
            dv_text part = DV_LIT(p.p, i);
            if (!dv_files_is_dir(part) && !dv_files_mkdir1(part)) return false;
        }
    }
    return dv_files_mkdir1(p);
}

static bool dv_files_append(dv_text p, dv_text t) {
#ifdef _WIN32
    wchar_t* w = dv_files_wpath(p); if (!w) return false;
    FILE* f = _wfopen(w, L"ab"); free(w);
#else
    char* c = dv_files_cpath(p); if (!c) return false;
    FILE* f = fopen(c, "ab"); free(c);
#endif
    if (!f) return false;
    bool ok = t.n == 0 || fwrite(t.p, 1, (size_t)t.n, f) == (size_t)t.n;
    if (fclose(f) != 0) ok = false;
    return ok;
}

// move/rename; refuses when `to` already exists (never replaces a file silently)
static bool dv_files_rename(dv_text from, dv_text to) {
    if (dv_files_exists(to) || !dv_files_exists(from)) return false;
#ifdef _WIN32
    wchar_t* a = dv_files_wpath(from); wchar_t* b = dv_files_wpath(to);
    bool ok = a && b && MoveFileExW(a, b, 0);
    free(a); free(b); return ok;
#else
    char* a = dv_files_cpath(from); char* b = dv_files_cpath(to);
    bool ok = a && b && rename(a, b) == 0;
    free(a); free(b); return ok;
#endif
}

// a file, or an EMPTY folder; never recursive
static bool dv_files_delete(dv_text p) {
    bool dir = dv_files_is_dir(p);
#ifdef _WIN32
    wchar_t* w = dv_files_wpath(p); if (!w) return false;
    bool ok = dir ? RemoveDirectoryW(w) : DeleteFileW(w);
    free(w); return ok;
#else
    char* c = dv_files_cpath(p); if (!c) return false;
    bool ok = (dir ? rmdir(c) : unlink(c)) == 0;
    free(c); return ok;
#endif
}

static dv_text dv_files_cwd(void) {
#ifdef _WIN32
    wchar_t b[4096]; DWORD n = GetCurrentDirectoryW(4096, b);
    if (n == 0 || n >= 4096) return dv_text_new("", 0);
    char* s = dv_narrow(b);
    for (char* q = s; *q; q++) if (*q == '\\') *q = '/';
    dv_text r = dv_text_new(s, (int64_t)strlen(s)); free(s); return r;
#else
    char b[4096]; if (!getcwd(b, sizeof b)) return dv_text_new("", 0);
    return dv_text_new(b, (int64_t)strlen(b));
#endif
}
