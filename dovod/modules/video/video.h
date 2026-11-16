// Module `video`, native side: a window that plays a media file.
// Windows only: playback is done by Media Foundation (MFPlay), which ships with Windows 7+,
// so a Dovod player is one .exe with no DLLs. (On other targets the compiler generates trapping stubs.)
//
// The program drives a poll loop: `video_event()` pumps the window for up to 100 ms and returns
// one event as Text: "tick" (nothing happened), "key:<NAME>" (with "ctrl+"/"shift+" before the name),
// "click", "dblclick", "wheel:up", "wheel:down", "drop:<path>\n<path>...", "loaded", "ended", "closed",
// "error:<message>".

#ifdef _WIN32
#define COBJMACROS
#include <commdlg.h>
#include <mfapi.h>
#include <mfplay.h>

#define DV_VQ 256
static dv_text dv_vq[DV_VQ];
static int dv_vq_head = 0, dv_vq_len = 0;
static HWND dv_vwin = NULL, dv_varea = NULL;
static HWND dv_vopen = NULL, dv_vprev = NULL, dv_vback = NULL, dv_vplay = NULL;
static HWND dv_vforward = NULL, dv_vnext = NULL, dv_vfullbtn = NULL;
static IMFPMediaPlayer* dv_vplayer = NULL;
static int dv_vfull = 0, dv_vmuted = 0, dv_vhasvideo = 0, dv_vpaused = 0;
static float dv_vvol = 1.0f, dv_vrate = 1.0f;
static WINDOWPLACEMENT dv_vplace = { sizeof(WINDOWPLACEMENT) };
static wchar_t* dv_vstatus = NULL;
static HFONT dv_vfont = NULL;
#define DV_VSTATUS 26
#define DV_VCONTROLS 42
#define DV_VSTRIP (DV_VSTATUS + DV_VCONTROLS)
#define DV_VOPEN 1001
#define DV_VPREV 1002
#define DV_VBACK 1003
#define DV_VPLAY 1004
#define DV_VFORWARD 1005
#define DV_VNEXT 1006
#define DV_VFULL 1007

// set DOVOD_VIDEO_LOG=C:\path\log.txt to record every event and every Media Foundation result
static FILE* dv_vlogf = NULL;
static void dv_vlog(const char* what, long hr) {
    static int tried = 0;
    if (!tried) { tried = 1; const char* p = getenv("DOVOD_VIDEO_LOG"); if (p && *p) dv_vlogf = fopen(p, "a"); }
    if (!dv_vlogf) return;
    fprintf(dv_vlogf, "%lld %s hr=0x%08lX\n", (long long)dv_now_ms(), what, (unsigned long)hr);
    fflush(dv_vlogf);
}

static void dv_vpush(const char* s) {
    dv_vlog(s, 0);
    if (dv_vq_len == DV_VQ) return;  // a full queue drops new events; the program is not reading
    dv_vq[(dv_vq_head + dv_vq_len) % DV_VQ] = dv_text_new(s, (int64_t)strlen(s));
    dv_vq_len++;
}
static void dv_vplay_label(void) {
    if (dv_vplay) SetWindowTextW(dv_vplay, dv_vpaused ? L"Играть" : L"Пауза");
}
static void dv_vcontrols_show(int show) {
    HWND hs[] = { dv_vopen, dv_vprev, dv_vback, dv_vplay, dv_vforward, dv_vnext, dv_vfullbtn };
    for (int i = 0; i < (int)(sizeof hs / sizeof hs[0]); i++) if (hs[i]) ShowWindow(hs[i], show ? SW_SHOW : SW_HIDE);
}

// ---- the callback object MFPlay reports to (a minimal COM object written in C)
static HRESULT STDMETHODCALLTYPE dv_cb_qi(IMFPMediaPlayerCallback* This, REFIID riid, void** out) {
    static const GUID iid_cb = {0x766c8ffb, 0x5fdb, 0x4fea, {0xa2, 0x8d, 0xb9, 0x12, 0x99, 0x6f, 0x51, 0xbd}};
    static const GUID iid_unk = {0x00000000, 0x0000, 0x0000, {0xc0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46}};
    if (IsEqualGUID(riid, &iid_cb) || IsEqualGUID(riid, &iid_unk)) { *out = This; return S_OK; }
    *out = NULL; return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE dv_cb_addref(IMFPMediaPlayerCallback* This) { (void)This; return 2; }
static ULONG STDMETHODCALLTYPE dv_cb_release(IMFPMediaPlayerCallback* This) { (void)This; return 1; }
static const char* dv_vhr_text(HRESULT hr) {
    switch ((unsigned long)hr) {
        case 0xC00D36C4UL: return "this file type is not supported";
        case 0xC00D5212UL: return "no decoder for this video or audio format is installed";
        case 0x80070002UL: return "file not found";
        case 0x80070005UL: return "access denied";
        case 0xC00D36E6UL: return "the file has no playable streams";
        default: return NULL;
    }
}
static void dv_vapply(void) {
    if (!dv_vplayer) return;
    IMFPMediaPlayer_SetVolume(dv_vplayer, dv_vvol);
    IMFPMediaPlayer_SetMute(dv_vplayer, dv_vmuted ? TRUE : FALSE);
    if (dv_vrate != 1.0f) IMFPMediaPlayer_SetRate(dv_vplayer, dv_vrate);
}
static void STDMETHODCALLTYPE dv_cb_event(IMFPMediaPlayerCallback* This, MFP_EVENT_HEADER* ev) {
    (void)This;
    { char b[64]; snprintf(b, sizeof b, "mfp-event type=%d state=%d", (int)ev->eEventType, (int)ev->eState); dv_vlog(b, ev->hrEvent); }
    if (FAILED(ev->hrEvent)) {
        char b[200]; const char* t = dv_vhr_text(ev->hrEvent);
        if (t) snprintf(b, sizeof b, "error:%s", t);
        else snprintf(b, sizeof b, "error:Media Foundation code 0x%08lX", (unsigned long)ev->hrEvent);
        dv_vpush(b);
        return;
    }
    if (ev->eEventType == MFP_EVENT_TYPE_MEDIAITEM_SET) {
        IMFPMediaItem* item = NULL; BOOL has = FALSE, sel = FALSE;
        BOOL ha = FALSE, hasel = FALSE;
        if (dv_vplayer && SUCCEEDED(IMFPMediaPlayer_GetMediaItem(dv_vplayer, &item)) && item) {
            IMFPMediaItem_HasVideo(item, &has, &sel);
            IMFPMediaItem_HasAudio(item, &ha, &hasel);   // DOVOD_VIDEO_LOG shows whether Windows even selects this file's audio stream
            IMFPMediaItem_Release(item);
        }
        { char b[128]; snprintf(b, sizeof b, "streams: video=%d(sel %d) audio=%d(sel %d)", (int)has, (int)sel, (int)ha, (int)hasel); dv_vlog(b, 0); }
        dv_vhasvideo = has && sel;
        if (!ha || !hasel) dv_vpush("noaudio");   // the program can tell the listener
        dv_vpaused = 0; dv_vplay_label();
        dv_vapply();
        if (dv_varea) InvalidateRect(dv_varea, NULL, TRUE);
        dv_vpush("loaded");
    } else if (ev->eEventType == MFP_EVENT_TYPE_PLAYBACK_ENDED) {
        dv_vpaused = 1; dv_vplay_label();
        dv_vpush("ended");
    }
}
static IMFPMediaPlayerCallbackVtbl dv_cb_vtbl = { dv_cb_qi, dv_cb_addref, dv_cb_release, dv_cb_event };
static IMFPMediaPlayerCallback dv_cb = { &dv_cb_vtbl };

// ---- window
static void dv_vlayout(void) {
    RECT r; GetClientRect(dv_vwin, &r);
    int strip = dv_vfull ? 0 : DV_VSTRIP;
    int h = r.bottom - strip; if (h < 0) h = 0;
    MoveWindow(dv_varea, 0, 0, r.right, h, TRUE);
    if (!dv_vfull) {
        int y = h + 5, x = 8, bh = DV_VCONTROLS - 10;
        MoveWindow(dv_vopen, x, y, 88, bh, TRUE); x += 94;
        MoveWindow(dv_vprev, x, y, 54, bh, TRUE); x += 60;
        MoveWindow(dv_vback, x, y, 54, bh, TRUE); x += 60;
        MoveWindow(dv_vplay, x, y, 72, bh, TRUE); x += 78;
        MoveWindow(dv_vforward, x, y, 54, bh, TRUE); x += 60;
        MoveWindow(dv_vnext, x, y, 54, bh, TRUE); x += 60;
        MoveWindow(dv_vfullbtn, x, y, 116, bh, TRUE);
    }
    if (dv_vplayer) IMFPMediaPlayer_UpdateVideo(dv_vplayer);
    RECT s = { 0, r.bottom - DV_VSTATUS, r.right, r.bottom }; InvalidateRect(dv_vwin, &s, TRUE);
}
static void dv_vkey(WPARAM vk) {
    char name[16] = {0};
    if (vk >= 'A' && vk <= 'Z') { name[0] = (char)vk; }
    else if (vk >= '0' && vk <= '9') { name[0] = (char)vk; }
    else if (vk >= VK_NUMPAD0 && vk <= VK_NUMPAD9) { name[0] = (char)('0' + (vk - VK_NUMPAD0)); }
    else if (vk >= VK_F1 && vk <= VK_F12) { snprintf(name, sizeof name, "F%d", (int)(vk - VK_F1 + 1)); }
    else {
        const char* n = NULL;
        switch (vk) {
            case VK_SPACE: n = "SPACE"; break;   case VK_LEFT: n = "LEFT"; break;
            case VK_RIGHT: n = "RIGHT"; break;   case VK_UP: n = "UP"; break;
            case VK_DOWN: n = "DOWN"; break;     case VK_PRIOR: n = "PGUP"; break;
            case VK_NEXT: n = "PGDN"; break;     case VK_HOME: n = "HOME"; break;
            case VK_END: n = "END"; break;       case VK_ESCAPE: n = "ESC"; break;
            case VK_RETURN: n = "ENTER"; break;  case VK_BACK: n = "BACKSPACE"; break;
            case VK_OEM_4: n = "["; break;       case VK_OEM_6: n = "]"; break;
            case VK_OEM_PLUS: case VK_ADD: n = "+"; break;
            case VK_OEM_MINUS: case VK_SUBTRACT: n = "-"; break;
            case VK_OEM_COMMA: n = ","; break;   case VK_OEM_PERIOD: n = "."; break;
            default: return;                     // modifiers alone and unknown keys are not events
        }
        snprintf(name, sizeof name, "%s", n);
    }
    char b[48];
    snprintf(b, sizeof b, "key:%s%s%s", (GetKeyState(VK_CONTROL) & 0x8000) ? "ctrl+" : "",
             (GetKeyState(VK_SHIFT) & 0x8000) ? "shift+" : "", name);
    dv_vpush(b);
}
static LRESULT CALLBACK dv_varea_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps; HDC dc = BeginPaint(h, &ps);
            if (dv_vplayer && dv_vhasvideo) IMFPMediaPlayer_UpdateVideo(dv_vplayer);
            else FillRect(dc, &ps.rcPaint, (HBRUSH)GetStockObject(BLACK_BRUSH));
            EndPaint(h, &ps);
            return 0;
        }
        case WM_ERASEBKGND: return 1;
        case WM_LBUTTONUP: dv_vpush("click"); SetFocus(dv_vwin); return 0;
        case WM_LBUTTONDBLCLK: dv_vpush("dblclick"); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}
static LRESULT CALLBACK dv_vwin_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_SIZE: if (dv_varea) dv_vlayout(); return 0;
        case WM_PAINT: {
            PAINTSTRUCT ps; HDC dc = BeginPaint(h, &ps);
            if (!dv_vfull) {
                RECT r; GetClientRect(h, &r); r.top = r.bottom - DV_VSTATUS;
                HBRUSH bg = CreateSolidBrush(RGB(32, 32, 36)); FillRect(dc, &r, bg); DeleteObject(bg);
                if (dv_vstatus) {
                    HGDIOBJ old = SelectObject(dc, dv_vfont);
                    SetBkMode(dc, TRANSPARENT); SetTextColor(dc, RGB(225, 225, 230));
                    r.left += 10; r.right -= 10;
                    DrawTextW(dc, dv_vstatus, -1, &r, DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
                    SelectObject(dc, old);
                }
            }
            EndPaint(h, &ps);
            return 0;
        }
        case WM_COMMAND:
            if (HIWORD(w) != BN_CLICKED) break;
            switch (LOWORD(w)) {
                case DV_VOPEN: dv_vpush("key:O"); break;
                case DV_VPREV: dv_vpush("key:P"); break;
                case DV_VBACK: dv_vpush("key:LEFT"); break;
                case DV_VPLAY: dv_vpush("key:SPACE"); break;
                case DV_VFORWARD: dv_vpush("key:RIGHT"); break;
                case DV_VNEXT: dv_vpush("key:N"); break;
                case DV_VFULL: dv_vpush("key:F"); break;
                default: return 0;
            }
            SetFocus(h); return 0;
        case WM_KEYDOWN: case WM_SYSKEYDOWN:
            if (m == WM_SYSKEYDOWN && w != VK_F10 && w != VK_RETURN) break;  // keep Alt+F4 working
            dv_vkey(w); return 0;
        case WM_MOUSEWHEEL: dv_vpush(GET_WHEEL_DELTA_WPARAM(w) > 0 ? "wheel:up" : "wheel:down"); return 0;
        case WM_DROPFILES: {  // one event for the whole drop: "drop:<path>\n<path>..."
            HDROP d = (HDROP)w; UINT n = DragQueryFileW(d, 0xFFFFFFFF, NULL, 0);
            dv_buf b = {0}; dv_buf_puts(&b, "drop:");
            for (UINT i = 0; i < n; i++) {
                UINT k = DragQueryFileW(d, i, NULL, 0);
                wchar_t* p = malloc(sizeof(wchar_t) * (k + 1));
                if (!p) continue;
                DragQueryFileW(d, i, p, k + 1);
                char* u = dv_narrow(p); free(p);
                if (i) dv_buf_puts(&b, "\n");
                dv_buf_puts(&b, u); free(u);
            }
            if (n) dv_vpush(b.p);
            free(b.p);
            DragFinish(d); SetForegroundWindow(h);
            return 0;
        }
        case WM_CLOSE: dv_vpush("closed"); ShowWindow(h, SW_HIDE); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}
static void dv_vinit(void) {
    if (dv_vwin) return;
    SetProcessDPIAware();
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    MFStartup(MF_VERSION, MFSTARTUP_FULL);
    HINSTANCE hi = GetModuleHandleW(NULL);
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = dv_vwin_proc; wc.hInstance = hi; wc.lpszClassName = L"DovodVideo";
    wc.hCursor = LoadCursor(NULL, IDC_ARROW); wc.hbrBackground = (HBRUSH)GetStockObject(BLACK_BRUSH);
    wc.hIcon = LoadIcon(NULL, IDI_APPLICATION);
    RegisterClassW(&wc);
    WNDCLASSW wa = {0};
    wa.style = CS_DBLCLKS; wa.lpfnWndProc = dv_varea_proc; wa.hInstance = hi; wa.lpszClassName = L"DovodVideoArea";
    wa.hCursor = LoadCursor(NULL, IDC_ARROW); wa.hbrBackground = (HBRUSH)GetStockObject(BLACK_BRUSH);
    RegisterClassW(&wa);
    int dpi = 96; HDC sdc = GetDC(NULL); if (sdc) { dpi = GetDeviceCaps(sdc, LOGPIXELSY); ReleaseDC(NULL, sdc); }
    dv_vfont = CreateFontW(-MulDiv(10, dpi, 72), 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0,
                           CLEARTYPE_QUALITY, 0, L"Segoe UI");
    int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
    int ww = sw * 2 / 3, wh = sh * 2 / 3;
    dv_vwin = CreateWindowExW(WS_EX_ACCEPTFILES, L"DovodVideo", L"Dovod", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                              (sw - ww) / 2, (sh - wh) / 2, ww, wh, NULL, NULL, hi, NULL);
    dv_varea = CreateWindowExW(0, L"DovodVideoArea", L"", WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, dv_vwin, NULL, hi, NULL);
    dv_vopen = CreateWindowW(L"BUTTON", L"Открыть", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 10, 10, dv_vwin, (HMENU)DV_VOPEN, hi, NULL);
    dv_vprev = CreateWindowW(L"BUTTON", L"Пред.", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 10, 10, dv_vwin, (HMENU)DV_VPREV, hi, NULL);
    dv_vback = CreateWindowW(L"BUTTON", L"-5 с", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 10, 10, dv_vwin, (HMENU)DV_VBACK, hi, NULL);
    dv_vplay = CreateWindowW(L"BUTTON", L"Пауза", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 10, 10, dv_vwin, (HMENU)DV_VPLAY, hi, NULL);
    dv_vforward = CreateWindowW(L"BUTTON", L"+5 с", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 10, 10, dv_vwin, (HMENU)DV_VFORWARD, hi, NULL);
    dv_vnext = CreateWindowW(L"BUTTON", L"След.", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 10, 10, dv_vwin, (HMENU)DV_VNEXT, hi, NULL);
    dv_vfullbtn = CreateWindowW(L"BUTTON", L"На весь экран", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 10, 10, dv_vwin, (HMENU)DV_VFULL, hi, NULL);
    HWND hs[] = { dv_vopen, dv_vprev, dv_vback, dv_vplay, dv_vforward, dv_vnext, dv_vfullbtn };
    for (int i = 0; i < (int)(sizeof hs / sizeof hs[0]); i++) SendMessageW(hs[i], WM_SETFONT, (WPARAM)dv_vfont, TRUE);
    DragAcceptFiles(dv_vwin, TRUE);
    dv_vlayout();
    ShowWindow(dv_vwin, SW_SHOW); UpdateWindow(dv_vwin); SetForegroundWindow(dv_vwin); SetFocus(dv_vwin);
}

// ---- the builtins
static bool dv_video_open(dv_text path) {
    dv_vinit();
    if (dv_vplayer) { IMFPMediaPlayer_Shutdown(dv_vplayer); IMFPMediaPlayer_Release(dv_vplayer); dv_vplayer = NULL; }
    dv_vhasvideo = 0;
    dv_vpaused = 0; dv_vplay_label();
    wchar_t* w = dv_wide(path.p, path.n);
    HRESULT hr = MFPCreateMediaPlayer(w, TRUE, 0, &dv_cb, dv_varea, &dv_vplayer);
    free(w);
    dv_vlog("open", hr);
    if (FAILED(hr)) {
        dv_vplayer = NULL;
        char b[200]; const char* t = dv_vhr_text(hr);
        if (t) snprintf(b, sizeof b, "error:%s", t);
        else snprintf(b, sizeof b, "error:Media Foundation code 0x%08lX", (unsigned long)hr);
        dv_vpush(b);
        InvalidateRect(dv_varea, NULL, TRUE);
        return false;
    }
    return true;
}
static O_Text dv_video_pick(void) {
    dv_vinit();
    O_Text r = {0};
    wchar_t buf[4096] = {0};
    OPENFILENAMEW o = {0};
    o.lStructSize = sizeof o; o.hwndOwner = dv_vwin; o.lpstrFile = buf; o.nMaxFile = 4096;
    o.lpstrFilter = L"Video and audio\0*.mp4;*.m4v;*.mkv;*.avi;*.wmv;*.mov;*.webm;*.3gp;*.ts;*.mts;*.mp3;*.m4a;*.wav;*.flac;*.wma;*.aac\0All files\0*.*\0";
    o.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_EXPLORER;
    if (!GetOpenFileNameW(&o)) return r;
    char* u = dv_narrow(buf);
    r.has = true; r.v = dv_text_new(u, (int64_t)strlen(u)); free(u);
    return r;
}
static dv_text dv_video_event(void) {
    dv_vinit();
    if (dv_vq_len == 0) {
        MsgWaitForMultipleObjects(0, NULL, FALSE, 100, QS_ALLINPUT);
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    if (dv_vq_len == 0) return dv_text_new("tick", 4);
    dv_text t = dv_vq[dv_vq_head]; dv_vq_head = (dv_vq_head + 1) % DV_VQ; dv_vq_len--;
    return t;
}
static void dv_video_pause(bool on) {
    dv_vinit();
    if (!dv_vplayer) return;
    dv_vpaused = on ? 1 : 0; dv_vplay_label();
    dv_vlog(on ? "pause" : "play", on ? IMFPMediaPlayer_Pause(dv_vplayer) : IMFPMediaPlayer_Play(dv_vplayer));
}
static void dv_video_seek(int64_t ms) {
    dv_vinit();
    if (!dv_vplayer) return;
    if (ms < 0) ms = 0;
    static const GUID pos100ns = {0};
    PROPVARIANT v; PropVariantInit(&v); v.vt = VT_I8; v.hVal.QuadPart = ms * 10000;
    char b[64]; snprintf(b, sizeof b, "seek %lld ms", (long long)ms);
    dv_vlog(b, IMFPMediaPlayer_SetPosition(dv_vplayer, &pos100ns, &v));
}
static int64_t dv_vtime(int dur) {
    if (!dv_vplayer) return 0;
    static const GUID pos100ns = {0};
    PROPVARIANT v; PropVariantInit(&v);
    HRESULT hr = dur ? IMFPMediaPlayer_GetDuration(dv_vplayer, &pos100ns, &v)
                     : IMFPMediaPlayer_GetPosition(dv_vplayer, &pos100ns, &v);
    int64_t r = 0;
    if (SUCCEEDED(hr) && (v.vt == VT_I8 || v.vt == VT_UI8)) r = (int64_t)(v.hVal.QuadPart / 10000);
    PropVariantClear(&v);
    return r < 0 ? 0 : r;
}
static int64_t dv_video_pos(void) { dv_vinit(); return dv_vtime(0); }
static int64_t dv_video_len(void) { dv_vinit(); return dv_vtime(1); }
static void dv_video_volume(int64_t pct) {
    dv_vinit(); dv_vvol = (float)pct / 100.0f;
    if (dv_vplayer) IMFPMediaPlayer_SetVolume(dv_vplayer, dv_vvol);
}
static void dv_video_mute(bool on) {
    dv_vinit(); dv_vmuted = on;
    if (dv_vplayer) IMFPMediaPlayer_SetMute(dv_vplayer, on ? TRUE : FALSE);
}
static void dv_video_rate(int64_t pct) {
    dv_vinit(); dv_vrate = (float)pct / 100.0f;
    if (dv_vplayer && FAILED(IMFPMediaPlayer_SetRate(dv_vplayer, dv_vrate))) dv_vpush("error:this file cannot play at that speed");
}
static void dv_video_fullscreen(bool on) {
    dv_vinit();
    if (on == (bool)dv_vfull) return;
    if (on) {
        MONITORINFO mi = { sizeof mi };
        GetWindowPlacement(dv_vwin, &dv_vplace);
        GetMonitorInfoW(MonitorFromWindow(dv_vwin, MONITOR_DEFAULTTOPRIMARY), &mi);
        dv_vfull = 1;
        dv_vcontrols_show(0);
        SetWindowLongW(dv_vwin, GWL_STYLE, WS_POPUP | WS_VISIBLE | WS_CLIPCHILDREN);
        SetWindowPos(dv_vwin, HWND_TOP, mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right - mi.rcMonitor.left,
                     mi.rcMonitor.bottom - mi.rcMonitor.top, SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    } else {
        dv_vfull = 0;
        dv_vcontrols_show(1);
        SetWindowLongW(dv_vwin, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE | WS_CLIPCHILDREN);
        SetWindowPlacement(dv_vwin, &dv_vplace);
        SetWindowPos(dv_vwin, NULL, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    }
    dv_vlayout();
    InvalidateRect(dv_vwin, NULL, TRUE);  // repaint all: the status strip appears / disappears
    if (dv_vplayer) IMFPMediaPlayer_UpdateVideo(dv_vplayer);  // a paused frame is redrawn at the new size
}
static void dv_video_status(dv_text t) {
    dv_vinit();
    wchar_t* w = dv_wide(t.p, t.n);
    if (dv_vstatus && wcscmp(dv_vstatus, w) == 0) { free(w); return; }  // unchanged: no repaint, no flicker
    free(dv_vstatus); dv_vstatus = w;
    RECT r; GetClientRect(dv_vwin, &r); r.top = r.bottom - DV_VSTATUS; InvalidateRect(dv_vwin, &r, TRUE);
}
static void dv_video_title(dv_text t) {
    dv_vinit();
    wchar_t* w = dv_wide(t.p, t.n); SetWindowTextW(dv_vwin, w); free(w);
}

#endif
