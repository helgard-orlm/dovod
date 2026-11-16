// Module `ble`, native side (win64): the LE advertisement watcher, reached from plain C.
// No C++ and no WinRT headers: the ABI below is transcribed by hand, every GUID and every vtable
// order cross-checked in two independent sources —
//   - interface IIDs and method order: wine's windows.devices.bluetooth.advertisement.idl
//     (wine-mirror) == windows-rs 0.48 generated bindings (microsoft/windows-rs); the advertisement
//     interfaces match MIDL order exactly, incl. Start/Stop between the filter props and the events;
//   - generic instantiation IIDs (TypedEventHandler<Watcher,Args> and <Watcher,StoppedArgs>):
//     computed with the documented SHA-1 derivation (namespace 11f47ad5-7b73-42c0-abae-878b1e16adee +
//     "pinterface({...};rc(...);rc(...))"); the ReceivedArgs one confirmed verbatim by gowinrt's
//     README example; IVector / IBufferByteAccess layouts from wine windows.foundation.collections.idl
//     and mingw robuffer.h;
//   - IActivationFactory 00000035-...-46, IInspectable af86e2e0-...: mingw-w64 headers.
// Raw events: "tick" | "off" | "adv\n<MAC AA:BB:CC:DD:EE:FF>\n<rssi>\n<name or ->\n<services or ->
//   <company:data or ->" — MAC upper-case, other hex lower-case; names single-line (bytes <0x20 and
//   0x7F become a space); a UUID that fits the standard base shortens to 4 hex, otherwise 32; the
//   first manufacturer block only, data cut to 31 bytes.
// Concurrency: Received/Stopped fire on threadpool threads; they only push into a bounded queue
// (drop oldest, silently) and signal. dv_ble_event waits on a condition variable for at most the
// asked time and hands out ONE event. The stop path unregisters, stops, then waits until the last
// callback left — a Release with a live callback would crash.
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <inspectable.h>

// ---- the ABI --------------------------------------------------------------------------

static const IID dv_ble_iid_actf     = {0x00000035,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};
static const IID dv_ble_iid_inspect  = {0xAF86E2E0,0xB12D,0x4C6A,{0x9C,0x5A,0xD7,0xAA,0x65,0x10,0x1E,0x90}};
static const IID dv_ble_iid_unknown  = {0x00000000,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};
static const IID dv_ble_iid_watcher  = {0xA6AC336F,0xF3D3,0x4297,{0x8D,0x6C,0xC8,0x1E,0xA6,0x62,0x3F,0x40}};
static const IID dv_ble_iid_teh_recv = {0x90EB4ECA,0xD465,0x5EA0,{0xA6,0x1C,0x03,0x3C,0x8C,0x5E,0xCE,0xF2}};
static const IID dv_ble_iid_teh_stop = {0x9936A4DB,0xDC99,0x55C3,{0x9E,0x9B,0xBF,0x48,0x54,0xBD,0x9E,0xAB}};
static const IID dv_ble_iid_agile    = {0x94EA2B94,0xE9CC,0x49E0,{0xC0,0xFF,0xEE,0x64,0xCA,0x8F,0x5B,0x90}};   // IAgileObject: event sources ask for it
static const IID dv_ble_iid_bufbyte  = {0x905A0FEF,0xBC53,0x11DF,{0x8C,0x49,0x00,0x1E,0x4F,0xC6,0x86,0xDA}};

typedef struct {
    // IInspectable
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *GetIids)(void*, ULONG*, IID**);
    HRESULT (WINAPI *GetRuntimeClassName)(void*, HSTRING*);
    HRESULT (WINAPI *GetTrustLevel)(void*, TrustLevel*);
    // IActivationFactory
    HRESULT (WINAPI *ActivateInstance)(void*, IInspectable**);
} dv_ble_actfVtbl;

typedef struct {
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *GetIids)(void*, ULONG*, IID**);
    HRESULT (WINAPI *GetRuntimeClassName)(void*, HSTRING*);
    HRESULT (WINAPI *GetTrustLevel)(void*, TrustLevel*);
    // IBluetoothLEAdvertisementWatcher
    HRESULT (WINAPI *get_MinSamplingInterval)(void*, int64_t*);
    HRESULT (WINAPI *get_MaxSamplingInterval)(void*, int64_t*);
    HRESULT (WINAPI *get_MinOutOfRangeTimeout)(void*, int64_t*);
    HRESULT (WINAPI *get_MaxOutOfRangeTimeout)(void*, int64_t*);
    HRESULT (WINAPI *get_Status)(void*, int32_t*);          // 0 created, 1 started, 2 stopping, 3 stopped, 4 aborted
    HRESULT (WINAPI *get_ScanningMode)(void*, int32_t*);
    HRESULT (WINAPI *put_ScanningMode)(void*, int32_t);      // 0 passive, 1 active
    HRESULT (WINAPI *get_SignalStrengthFilter)(void*, void**);
    HRESULT (WINAPI *put_SignalStrengthFilter)(void*, void*);
    HRESULT (WINAPI *get_AdvertisementFilter)(void*, void**);
    HRESULT (WINAPI *put_AdvertisementFilter)(void*, void*);
    HRESULT (WINAPI *Start)(void*);
    HRESULT (WINAPI *Stop)(void*);
    HRESULT (WINAPI *add_Received)(void*, void*, int64_t*);
    HRESULT (WINAPI *remove_Received)(void*, int64_t);
    HRESULT (WINAPI *add_Stopped)(void*, void*, int64_t*);
    HRESULT (WINAPI *remove_Stopped)(void*, int64_t);
} dv_ble_watcherVtbl;

typedef struct {
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *GetIids)(void*, ULONG*, IID**);
    HRESULT (WINAPI *GetRuntimeClassName)(void*, HSTRING*);
    HRESULT (WINAPI *GetTrustLevel)(void*, TrustLevel*);
    // IBluetoothLEAdvertisementReceivedEventArgs
    HRESULT (WINAPI *get_RawSignalStrengthInDBm)(void*, int16_t*);   // a plain INT16 (NOT IReference)
    HRESULT (WINAPI *get_BluetoothAddress)(void*, uint64_t*);
    HRESULT (WINAPI *get_AdvertisementType)(void*, int32_t*);
    HRESULT (WINAPI *get_Timestamp)(void*, int64_t*);
    HRESULT (WINAPI *get_Advertisement)(void*, void**);
} dv_ble_argsVtbl;

typedef struct {
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *GetIids)(void*, ULONG*, IID**);
    HRESULT (WINAPI *GetRuntimeClassName)(void*, HSTRING*);
    HRESULT (WINAPI *GetTrustLevel)(void*, TrustLevel*);
    // IBluetoothLEAdvertisement
    HRESULT (WINAPI *get_Flags)(void*, void**);
    HRESULT (WINAPI *put_Flags)(void*, void*);
    HRESULT (WINAPI *get_LocalName)(void*, HSTRING*);
    HRESULT (WINAPI *put_LocalName)(void*, HSTRING);
    HRESULT (WINAPI *get_ServiceUuids)(void*, void**);               // IVector<GUID>
    HRESULT (WINAPI *get_ManufacturerData)(void*, void**);           // IVector<BluetoothLEManufacturerData>
    HRESULT (WINAPI *get_DataSections)(void*, void**);
    HRESULT (WINAPI *GetManufacturerDataByCompanyId)(void*, uint16_t, void**);
    HRESULT (WINAPI *GetSectionsByType)(void*, uint8_t, void**);
} dv_ble_advVtbl;

typedef struct {
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *GetIids)(void*, ULONG*, IID**);
    HRESULT (WINAPI *GetRuntimeClassName)(void*, HSTRING*);
    HRESULT (WINAPI *GetTrustLevel)(void*, TrustLevel*);
    // IVector<T> — only GetAt/get_Size are used (reading)
    HRESULT (WINAPI *GetAt)(void*, uint32_t, void*);
    HRESULT (WINAPI *get_Size)(void*, uint32_t*);
    HRESULT (WINAPI *GetView)(void*, void**);
    HRESULT (WINAPI *IndexOf)(void*, void*, uint32_t*, int32_t*);
    HRESULT (WINAPI *SetAt)(void*, uint32_t, void*);
    HRESULT (WINAPI *InsertAt)(void*, uint32_t, void*);
    HRESULT (WINAPI *RemoveAt)(void*, uint32_t);
    HRESULT (WINAPI *Append)(void*, void*);
    HRESULT (WINAPI *RemoveAtEnd)(void*);
    HRESULT (WINAPI *Clear)(void*);
    HRESULT (WINAPI *GetMany)(void*, uint32_t, uint32_t, void*, uint32_t*);
    HRESULT (WINAPI *ReplaceAll)(void*, uint32_t, void*);
} dv_ble_vectorVtbl;

typedef struct {
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *GetIids)(void*, ULONG*, IID**);
    HRESULT (WINAPI *GetRuntimeClassName)(void*, HSTRING*);
    HRESULT (WINAPI *GetTrustLevel)(void*, TrustLevel*);
    // IBluetoothLEManufacturerData
    HRESULT (WINAPI *get_CompanyId)(void*, uint16_t*);
    HRESULT (WINAPI *put_CompanyId)(void*, uint16_t);
    HRESULT (WINAPI *get_Data)(void*, void**);                       // IBuffer
    HRESULT (WINAPI *put_Data)(void*, void*);
} dv_ble_mfdVtbl;

typedef struct {
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *Buffer)(void*, uint8_t**);                      // IBufferByteAccess
} dv_ble_bufbyteVtbl;

typedef struct {
    HRESULT (WINAPI *QueryInterface)(void*, REFIID, void**);
    ULONG   (WINAPI *AddRef)(void*);
    ULONG   (WINAPI *Release)(void*);
    HRESULT (WINAPI *GetIids)(void*, ULONG*, IID**);
    HRESULT (WINAPI *GetRuntimeClassName)(void*, HSTRING*);
    HRESULT (WINAPI *GetTrustLevel)(void*, TrustLevel*);
    // IBuffer
    HRESULT (WINAPI *get_Capacity)(void*, uint32_t*);
    HRESULT (WINAPI *get_Length)(void*, uint32_t*);
    HRESULT (WINAPI *put_Length)(void*, uint32_t);
} dv_ble_ibufVtbl;

// ---- state ---------------------------------------------------------------------------

#define DV_BLE_Q 256
static dv_text dv_ble_q[DV_BLE_Q];
static int dv_ble_qh, dv_ble_qn;
static CRITICAL_SECTION dv_ble_cs;
static CONDITION_VARIABLE dv_ble_cv;
static int dv_ble_made;                 // cs/cv exist
static int dv_ble_ro;                   // RoInitialize done (kept forever: a flag, not an undo)
static void* dv_ble_w;                  // the watcher
static int64_t dv_ble_tok_recv, dv_ble_tok_stop;
static bool dv_ble_watching, dv_ble_stopping;
static volatile LONG dv_ble_busy;       // callbacks in flight (the stop handshake waits for 0)
static volatile LONG dv_ble_dbg = 5;    // the first N shouts leave a trace (crash hunting)
static HANDLE dv_ble_lf = INVALID_HANDLE_VALUE;   // blescan.log next to the exe, opened once

// the start steps and the first events leave a trail; a hard crash says where it happened
static void dv_ble_say(const char* what) {
    char b[280];
    int n = snprintf(b, sizeof b, "[%lu] %s\n", (unsigned long)GetCurrentThreadId(), what);
    if (dv_ble_lf != INVALID_HANDLE_VALUE && n > 0) {
        DWORD w = 0;
        WriteFile(dv_ble_lf, b, (DWORD)n, &w, NULL);   // no CRT allocation: safe from any thread
    }
    EnterCriticalSection(&dv_ble_cs);
    fprintf(stderr, "ble: %s\n", what);
    fflush(stderr);
    LeaveCriticalSection(&dv_ble_cs);
}
static LONG WINAPI dv_ble_crash(EXCEPTION_POINTERS* ep) {   // no cs here: the crash may hold it
    DWORD code = ep && ep->ExceptionRecord ? ep->ExceptionRecord->ExceptionCode : 0;
    char* addr = (char*)(ep && ep->ExceptionRecord ? ep->ExceptionRecord->ExceptionAddress : NULL);
    ULONG_PTR kind = ep && ep->ExceptionRecord && ep->ExceptionRecord->NumberParameters >= 1
                     ? ep->ExceptionRecord->ExceptionInformation[0] : 9;
    char* target = ep && ep->ExceptionRecord && ep->ExceptionRecord->NumberParameters >= 2
                   ? (char*)ep->ExceptionRecord->ExceptionInformation[1] : NULL;
    const char* rw = kind == 0 ? "read" : (kind == 1 ? "write" : (kind == 8 ? "EXEC" : "?"));
    char where[400] = "";
    const char* base = "unknown";
    HMODULE m = NULL;
    if (addr && GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                    addr, &m) && m) {
        char path[MAX_PATH * 2];
        DWORD n = GetModuleFileNameA(m, path, sizeof path);
        if (n && n < sizeof path) {
            const char* c = path + n;
            while (c > path && c[-1] != '\\' && c[-1] != '/') { c--; }
            base = c;
        }
        snprintf(where, sizeof where, " in %s+0x%llx", base, (unsigned long long)(addr - (char*)m));
    }
    char twhere[200] = "";
    if (target) {
        HMODULE tm = NULL;
        if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               target, &tm) && tm) {
            char p2[MAX_PATH * 2];
            DWORD n2 = GetModuleFileNameA(tm, p2, sizeof p2);
            if (n2 && n2 < sizeof p2) {
                const char* c2 = p2 + n2;
                while (c2 > p2 && c2[-1] != '\\' && c2[-1] != '/') { c2--; }
                snprintf(twhere, sizeof twhere, " target in %s+0x%llx", c2,
                         (unsigned long long)(target - (char*)tm));
            }
        } else {
            snprintf(twhere, sizeof twhere, " target unmapped (heap or garbage)");
        }
    }
    char b[768];
    int len = snprintf(b, sizeof b,
        "CRASH code=%08lx thread=%lu %s addr=%p%s%s\n",
        (unsigned long)code, (unsigned long)GetCurrentThreadId(), rw, addr, where, twhere);
    if (dv_ble_lf != INVALID_HANDLE_VALUE && len > 0) {
        DWORD w = 0;
        WriteFile(dv_ble_lf, b, (DWORD)len, &w, NULL);
    }
    fprintf(stderr, "ble: %s", b);
    fflush(stderr);
    return EXCEPTION_CONTINUE_SEARCH;
}

static void dv_ble_ensure(void) {
    if (!dv_ble_made) {
        InitializeCriticalSection(&dv_ble_cs);
        InitializeConditionVariable(&dv_ble_cv);
        SetUnhandledExceptionFilter(dv_ble_crash);
        char path[MAX_PATH * 2];
        DWORD n = GetModuleFileNameA(NULL, path, sizeof path);
        const char* tail = "blescan.log";
        if (n && n < sizeof path) {
            char* c = path + n;
            while (c > path && c[-1] != '\\' && c[-1] != '/') { c--; }
            snprintf(c, (size_t)(path + sizeof path - c), "%s", tail);
        } else {
            snprintf(path, sizeof path, "%s", tail);
        }
        dv_ble_lf = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        dv_ble_made = 1;
    }
}

static void dv_ble_say(const char* what);

static void dv_ble_push(dv_text ev) {
    EnterCriticalSection(&dv_ble_cs);
    if (dv_ble_qn == DV_BLE_Q) {
        free(dv_ble_q[dv_ble_qh].p);
        dv_ble_qh = (dv_ble_qh + 1) % DV_BLE_Q;
        dv_ble_qn--;
    }
    dv_ble_q[(dv_ble_qh + dv_ble_qn) % DV_BLE_Q] = ev;
    dv_ble_qn++;
    WakeConditionVariable(&dv_ble_cv);
    LeaveCriticalSection(&dv_ble_cs);
}

// a callback finished: count it down and let the stop handshake see 0
static void dv_ble_leave(void) {
    EnterCriticalSection(&dv_ble_cs);
    InterlockedDecrement(&dv_ble_busy);
    WakeConditionVariable(&dv_ble_cv);
    LeaveCriticalSection(&dv_ble_cs);
}

// ---- text helpers --------------------------------------------------------------------

// UTF-16 -> UTF-8 (surrogates handled); bytes below 0x20 and 0x7F become a space, so a name is one line
static void dv_ble_utf8(dv_buf* b, const wchar_t* w, UINT32 n) {
    for (UINT32 i = 0; i < n; ) {
        unsigned c = (unsigned)w[i++];
        if (c >= 0xD800 && c <= 0xDBFF && i < n && (unsigned)w[i] >= 0xDC00 && (unsigned)w[i] <= 0xDFFF)
            c = 0x10000 + ((c - 0xD800) << 10) + ((unsigned)w[i++] - 0xDC00);
        else if (c >= 0xD800 && c <= 0xDFFF) { dv_buf_put(b, " ", 1); continue; }   // a lone surrogate -> a space, not CESU-8
        char o[4];
        int m = 0;
        if (c < 0x80) { o[0] = (char)c; m = 1; }
        else if (c < 0x800) { o[0] = (char)(0xC0 | (c >> 6)); o[1] = (char)(0x80 | (c & 0x3F)); m = 2; }
        else if (c < 0x10000) { o[0] = (char)(0xE0 | (c >> 12)); o[1] = (char)(0x80 | ((c >> 6) & 0x3F)); o[2] = (char)(0x80 | (c & 0x3F)); m = 3; }
        else { o[0] = (char)(0xF0 | (c >> 18)); o[1] = (char)(0x80 | ((c >> 12) & 0x3F)); o[2] = (char)(0x80 | ((c >> 6) & 0x3F)); o[3] = (char)(0x80 | (c & 0x3F)); m = 4; }
        for (int k = 0; k < m; k++) {
            unsigned char u = (unsigned char)o[k];
            if (u < 0x20 || u == 0x7F) { dv_buf_put(b, " ", 1); } else { dv_buf_put(b, o + k, 1); }
        }
    }
}

static const BYTE dv_ble_base[8] = {0x80,0x00,0x00,0x80,0x5F,0x9B,0x34,0xFB};  // tail of 0000xxxx-0000-1000-8000-00805f9b34fb

// a UUID that fits the standard base -> its 4 hex digits; anything else -> 32 hex digits
static void dv_ble_uuid(dv_buf* b, const GUID* g) {
    char s[40];
    if (g->Data1 <= 0xFFFF && g->Data2 == 0 && g->Data3 == 0x1000 && memcmp(g->Data4, dv_ble_base, 8) == 0) {
        snprintf(s, sizeof s, "%04x", (unsigned)(g->Data1 & 0xFFFF));
        dv_buf_puts(b, s);
        return;
    }
    snprintf(s, sizeof s, "%08x%04x%04x%02x%02x%02x%02x%02x%02x%02x%02x",
             (unsigned)g->Data1, (unsigned)g->Data2, (unsigned)g->Data3,
             g->Data4[0], g->Data4[1], g->Data4[2], g->Data4[3],
             g->Data4[4], g->Data4[5], g->Data4[6], g->Data4[7]);
    dv_buf_puts(b, s);
}

// ---- the two handlers: COM objects made in C --------------------------------------------

typedef struct { const void** vtbl; } dv_ble_hobj;
static dv_ble_hobj dv_ble_recv_obj, dv_ble_stop_obj;   // defined below, with their vtables

static ULONG  WINAPI dv_ble_h_add(void* self)  { (void)self; return 1; }   // static objects: never freed
static ULONG  WINAPI dv_ble_h_rel(void* self)  { (void)self; return 1; }
// the honest answer: a real array (the caller frees it with CoTaskMemFree). The old
// "S_OK, count 0, pointer NULL" is legal COM — but their delivery code read through the
// NULL (crash: read at 0x10), so no more empty answers.
static HRESULT WINAPI dv_ble_h_iids(void* self, ULONG* n, IID** ids) {
    if (!n || !ids) return E_POINTER;
    IID* a = (IID*)CoTaskMemAlloc(sizeof(IID) * 4);
    if (!a) { *n = 0; *ids = NULL; return E_OUTOFMEMORY; }
    a[0] = dv_ble_iid_unknown;
    a[1] = dv_ble_iid_inspect;
    a[2] = (self == (void*)&dv_ble_recv_obj) ? dv_ble_iid_teh_recv : dv_ble_iid_teh_stop;
    a[3] = dv_ble_iid_agile;
    *n = 4;
    *ids = a;
    return S_OK;
}
static HRESULT WINAPI dv_ble_h_trust(void* self, TrustLevel* t) { (void)self; if (t) *t = 0; return S_OK; }
static HRESULT WINAPI dv_ble_h_name(void* self, HSTRING* out) {
    (void)self;
    if (!out) return E_POINTER;
    static const wchar_t n[] = L"Windows.Foundation.TypedEventHandler`2";
    return WindowsCreateString(n, (UINT32)(sizeof n / 2 - 1), out);
}

static volatile LONG dv_ble_qidbg = 24;   // the first N QI calls get a trace

static HRESULT WINAPI dv_ble_qi_recv(void* self, REFIID riid, void** out) {
    if (!out) return E_POINTER;
    bool hit = IsEqualIID(riid, &dv_ble_iid_unknown) || IsEqualIID(riid, &dv_ble_iid_inspect) ||
               IsEqualIID(riid, &dv_ble_iid_teh_recv) || IsEqualIID(riid, &dv_ble_iid_agile);
    if (dv_ble_qidbg > 0 && InterlockedDecrement(&dv_ble_qidbg) >= 0) {
        char w[96];
        snprintf(w, sizeof w, "qi recv riid=%08lx -> %s",
                 riid ? (unsigned long)riid->Data1 : 0, hit ? "S_OK" : "E_NOINTERFACE");
        dv_ble_say(w);
    }
    if (hit) { *out = self; return S_OK; }
    return E_NOINTERFACE;
}

static HRESULT WINAPI dv_ble_qi_stop(void* self, REFIID riid, void** out) {
    if (!out) return E_POINTER;
    bool hit = IsEqualIID(riid, &dv_ble_iid_unknown) || IsEqualIID(riid, &dv_ble_iid_inspect) ||
               IsEqualIID(riid, &dv_ble_iid_teh_stop) || IsEqualIID(riid, &dv_ble_iid_agile);
    if (dv_ble_qidbg > 0 && InterlockedDecrement(&dv_ble_qidbg) >= 0) {
        char w[96];
        snprintf(w, sizeof w, "qi stop riid=%08lx -> %s",
                 riid ? (unsigned long)riid->Data1 : 0, hit ? "S_OK" : "E_NOINTERFACE");
        dv_ble_say(w);
    }
    if (hit) { *out = self; return S_OK; }
    return E_NOINTERFACE;
}

// one shout -> "adv\nMAC\nrssi\nname\nservices\nmanuf" (empty name/services/manuf are "-")
static HRESULT WINAPI dv_ble_adv_invoke(void* self, void* sender, void* args) {
    (void)self; (void)sender;
    InterlockedIncrement(&dv_ble_busy);
    int dbg = 0;
    if (dv_ble_dbg > 0) { dbg = (int)InterlockedDecrement(&dv_ble_dbg) + 1; }
    if (dbg) { dv_ble_say("shout: arrived"); }
    if (args) {
        int16_t rssi = 0;
        uint64_t mac = 0;
        void* ad = NULL;
        dv_ble_argsVtbl* av = *(dv_ble_argsVtbl**)args;
        av->get_RawSignalStrengthInDBm(args, &rssi);
        av->get_BluetoothAddress(args, &mac);
        av->get_Advertisement(args, &ad);
        if (dbg) { dv_ble_say(ad ? "shout: args read" : "shout: no ad"); }

        dv_buf b = {0};
        char s[64];
        dv_buf_puts(&b, "adv\n");
        snprintf(s, sizeof s, "%02X:%02X:%02X:%02X:%02X:%02X",
                 (unsigned)((mac >> 40) & 0xFF), (unsigned)((mac >> 32) & 0xFF), (unsigned)((mac >> 24) & 0xFF),
                 (unsigned)((mac >> 16) & 0xFF), (unsigned)((mac >> 8) & 0xFF), (unsigned)(mac & 0xFF));
        dv_buf_puts(&b, s);
        snprintf(s, sizeof s, "\n%d\n", (int)rssi);
        dv_buf_puts(&b, s);

        // name
        int have_name = 0;
        if (ad) {
            dv_ble_advVtbl* dv = *(dv_ble_advVtbl**)ad;
            HSTRING h = NULL;
            dv->get_LocalName(ad, &h);
            UINT32 n = 0;
            if (h) {
                const wchar_t* w = WindowsGetStringRawBuffer(h, &n);
                if (n) { dv_ble_utf8(&b, w, n); have_name = 1; }
                WindowsDeleteString(h);
            }
            if (dbg) { dv_ble_say("shout: name done"); }
        }
        if (!have_name) { dv_buf_puts(&b, "-"); }
        dv_buf_puts(&b, "\n");

        // services: the declared abilities (short UUIDs, comma-separated)
        int have_svc = 0;
        if (ad) {
            dv_ble_advVtbl* dv = *(dv_ble_advVtbl**)ad;
            void* vec = NULL;
            dv->get_ServiceUuids(ad, &vec);
            if (vec) {
                dv_ble_vectorVtbl* vv = *(dv_ble_vectorVtbl**)vec;
                uint32_t n = 0;
                vv->get_Size(vec, &n);
                for (uint32_t i = 0; i < n; i++) {
                    GUID g;
                    memset(&g, 0, sizeof g);
                    if (SUCCEEDED(vv->GetAt(vec, i, &g))) {
                        if (have_svc) { dv_buf_puts(&b, ","); }
                        dv_ble_uuid(&b, &g);
                        have_svc = 1;
                    }
                }
                ((dv_ble_vectorVtbl*)*(void**)vec)->Release(vec);
            }
            if (dbg) { dv_ble_say("shout: services done"); }
        }
        if (!have_svc) { dv_buf_puts(&b, "-"); }
        dv_buf_puts(&b, "\n");

        // the maker's block: the first one, "company:data" (data cut to 31 bytes)
        int have_mfd = 0;
        if (ad) {
            dv_ble_advVtbl* dv = *(dv_ble_advVtbl**)ad;
            void* vec = NULL;
            dv->get_ManufacturerData(ad, &vec);
            if (vec) {
                dv_ble_vectorVtbl* vv = *(dv_ble_vectorVtbl**)vec;
                uint32_t n = 0;
                vv->get_Size(vec, &n);
                if (n > 0) {
                    void* md = NULL;
                    if (SUCCEEDED(vv->GetAt(vec, 0, &md)) && md) {
                        dv_ble_mfdVtbl* mv = *(dv_ble_mfdVtbl**)md;
                        uint16_t cid = 0;
                        void* buf = NULL;
                        mv->get_CompanyId(md, &cid);
                        mv->get_Data(md, &buf);
                        snprintf(s, sizeof s, "%04x:", (unsigned)cid);
                        dv_buf_puts(&b, s);
                        if (buf) {
                            uint32_t blen = 0;
                            ((dv_ble_ibufVtbl*)*(void**)buf)->get_Length(buf, &blen);
                            void* bba = NULL;
                            if (SUCCEEDED(((dv_ble_bufbyteVtbl*)*(void**)buf)->QueryInterface(buf, &dv_ble_iid_bufbyte, &bba)) && bba) {
                                uint8_t* bytes = NULL;
                                ((dv_ble_bufbyteVtbl*)*(void**)bba)->Buffer(bba, &bytes);
                                if (bytes) {
                                    int lim = (int)blen;
                                    if (lim > 31) { lim = 31; }
                                    for (int k = 0; k < lim; k++) {
                                        snprintf(s, sizeof s, "%02x", (unsigned)bytes[k]);
                                        dv_buf_puts(&b, s);
                                    }
                                }
                                ((dv_ble_bufbyteVtbl*)*(void**)bba)->Release(bba);
                            }
                            ((dv_ble_bufbyteVtbl*)*(void**)buf)->Release(buf);
                        }
                        have_mfd = 1;
                        mv->Release(md);
                    }
                }
                ((dv_ble_vectorVtbl*)*(void**)vec)->Release(vec);
            }
        }
        if (!have_mfd) { dv_buf_puts(&b, "-"); }
        if (dbg) { dv_ble_say("shout: manuf done"); }

        if (ad) { ((dv_ble_advVtbl*)*(void**)ad)->Release(ad); }
        if (dbg) { dv_ble_say("shout: built"); }
        dv_ble_push(dv_buf_take(&b));
        if (dbg) { dv_ble_say("shout: pushed"); }
    }
    dv_ble_leave();
    return S_OK;
}

// the watcher stopped. "off" only when it was not us who stopped it — the radio went away
static HRESULT WINAPI dv_ble_stop_invoke(void* self, void* sender, void* args) {
    (void)self; (void)sender; (void)args;
    InterlockedIncrement(&dv_ble_busy);
    int say = 0;
    EnterCriticalSection(&dv_ble_cs);
    if (dv_ble_watching && !dv_ble_stopping) { dv_ble_watching = 0; say = 1; }
    LeaveCriticalSection(&dv_ble_cs);
    if (say) { dv_ble_push(dv_text_new("off", 3)); }
    dv_ble_leave();
    return S_OK;
}

static const void* dv_ble_recv_vt[7] = {
    (const void*)dv_ble_qi_recv, (const void*)dv_ble_h_add, (const void*)dv_ble_h_rel,
    (const void*)dv_ble_h_iids, (const void*)dv_ble_h_name, (const void*)dv_ble_h_trust,
    (const void*)dv_ble_adv_invoke };
static const void* dv_ble_stop_vt[7] = {
    (const void*)dv_ble_qi_stop, (const void*)dv_ble_h_add, (const void*)dv_ble_h_rel,
    (const void*)dv_ble_h_iids, (const void*)dv_ble_h_name, (const void*)dv_ble_h_trust,
    (const void*)dv_ble_stop_invoke };
static dv_ble_hobj dv_ble_recv_obj = { dv_ble_recv_vt };
static dv_ble_hobj dv_ble_stop_obj = { dv_ble_stop_vt };

// ---- the three entry points ------------------------------------------------------------

static void dv_ble_scan_stop(void);

static bool dv_ble_scan_start(bool active) {
    dv_ble_ensure();
    if (dv_ble_w) { dv_ble_scan_stop(); }            // restart cleanly (e.g. after a spontaneous "off")
    dv_ble_ensure();
    { char w[192];
      snprintf(w, sizeof w, "scan_start: image=%p recv_obj=%p recv_vt=%p stop_obj=%p",
               (void*)GetModuleHandleA(NULL), (void*)&dv_ble_recv_obj, (void*)dv_ble_recv_vt, (void*)&dv_ble_stop_obj);
      dv_ble_say(w); }
    if (!dv_ble_ro) {
        HRESULT hr = RoInitialize(RO_INIT_MULTITHREADED);
        if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) { return false; }
        dv_ble_ro = 1;                                // never uninitialized: later callers rely on it
    }
    static const wchar_t cls[] = L"Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher";
    HSTRING h = NULL;
    if (FAILED(WindowsCreateString(cls, (UINT32)(sizeof cls / 2 - 1), &h))) { return false; }
    void* actf = NULL;
    HRESULT hr = RoGetActivationFactory(h, &dv_ble_iid_actf, &actf);
    WindowsDeleteString(h);
    if (FAILED(hr) || !actf) { return false; }
    IInspectable* insp = NULL;
    hr = ((dv_ble_actfVtbl*)*(void**)actf)->ActivateInstance(actf, &insp);
    ((dv_ble_actfVtbl*)*(void**)actf)->Release(actf);
    if (FAILED(hr) || !insp) { return false; }
    dv_ble_say("factory made");
    void* w = NULL;
    hr = ((dv_ble_actfVtbl*)*(void**)insp)->QueryInterface(insp, &dv_ble_iid_watcher, &w);
    ((dv_ble_actfVtbl*)*(void**)insp)->Release(insp);   // any IInspectable-shaped vtable: Release sits at slot 2
    if (FAILED(hr) || !w) { return false; }

    dv_ble_watcherVtbl* wv = *(dv_ble_watcherVtbl**)w;
    int64_t tr = 0, ts = 0;
    dv_ble_say("watcher made");
    if (FAILED(hr) || !w) { dv_ble_say("QI to the watcher failed"); }
    dv_ble_stopping = 0;
    HRESULT hr1 = wv->put_ScanningMode(w, active ? 1 : 0);
    HRESULT hr2 = wv->add_Received(w, &dv_ble_recv_obj, &tr);
    HRESULT hr3 = wv->add_Stopped(w, &dv_ble_stop_obj, &ts);
    if (FAILED(hr1) || FAILED(hr2) || FAILED(hr3)) {
        // a failed registration would "work" silently: started forever, not one event
        char why[112];
        snprintf(why, sizeof why, "registration failed: mode=%08lx recv=%08lx stop=%08lx",
                 (unsigned long)hr1, (unsigned long)hr2, (unsigned long)hr3);
        dv_ble_say(why);
        wv->remove_Received(w, tr);
        wv->remove_Stopped(w, ts);
        wv->Release(w);
        return false;
    }
    dv_ble_tok_recv = tr;
    dv_ble_tok_stop = ts;
    dv_ble_say("handlers on");
    if (FAILED(wv->Start(w))) {
        wv->remove_Received(w, tr);
        wv->remove_Stopped(w, ts);
        wv->Release(w);
        return false;
    }
    int32_t st = 0;
    for (int i = 0; i < 60; i++) {                     // up to ~3 s for a slow radio (breaks on success)
        wv->get_Status(w, &st);
        if (st == 1) { break; }                        // started
        if (st == 4) {                                 // aborted: no radio / off / blocked
            wv->remove_Received(w, tr);
            wv->remove_Stopped(w, ts);
            wv->Release(w);
            return false;
        }
        Sleep(50);
    }
    if (st != 1) {
        wv->Stop(w);
        wv->remove_Received(w, tr);
        wv->remove_Stopped(w, ts);
        wv->Release(w);
        return false;
    }
    if (st == 4) { dv_ble_say("start aborted (status 4): radio off/blocked"); }
    else if (st != 1) { dv_ble_say("start timed out: status not Started in 3 s"); }
    else { dv_ble_say("STARTED"); }
    dv_ble_w = w;
    dv_ble_watching = true;
    // drop a stale "off" of the dead session: it would be advice about a radio that is back
    dv_text tmp[DV_BLE_Q];
    int tn = 0;
    for (int k = 0; k < dv_ble_qn; k++) {
        dv_text e = dv_ble_q[(dv_ble_qh + k) % DV_BLE_Q];
        if (e.n == 3 && memcmp(e.p, "off", 3) == 0) { free(e.p); }
        else { tmp[tn++] = e; }
    }
    EnterCriticalSection(&dv_ble_cs);
    for (int k = 0; k < tn; k++) { dv_ble_q[k] = tmp[k]; }
    dv_ble_qh = 0;
    dv_ble_qn = tn;
    LeaveCriticalSection(&dv_ble_cs);
    return true;
}

static void dv_ble_scan_stop(void) {
    if (!dv_ble_made || !dv_ble_w) { return; }
    dv_ble_stopping = 1;                               // the Stopped event must not say "off"
    void* w = dv_ble_w;
    dv_ble_watcherVtbl* wv = *(dv_ble_watcherVtbl**)w;
    if (dv_ble_watching) {
        wv->remove_Received(w, dv_ble_tok_recv);
        wv->remove_Stopped(w, dv_ble_tok_stop);
        wv->Stop(w);
        dv_ble_watching = 0;
    }
    // wait until the last callback left: a Release with a live callback would crash
    ULONGLONG deadline = GetTickCount64() + 2000;
    EnterCriticalSection(&dv_ble_cs);
    while (dv_ble_busy > 0 && GetTickCount64() < deadline) {
        SleepConditionVariableCS(&dv_ble_cv, &dv_ble_cs, 50);
    }
    LeaveCriticalSection(&dv_ble_cs);
    wv->Release(w);
    dv_ble_w = NULL;
    dv_ble_stopping = 0;
}

static dv_text dv_ble_event(int64_t timeout) {
    if (!dv_ble_made) { return dv_text_new("tick", 4); }
    EnterCriticalSection(&dv_ble_cs);
    ULONGLONG deadline = GetTickCount64() + (timeout > 0 ? (ULONGLONG)timeout : 0);
    while (!dv_ble_qn) {
        ULONGLONG now = GetTickCount64();
        if (now >= deadline) { break; }
        SleepConditionVariableCS(&dv_ble_cv, &dv_ble_cs, (DWORD)(deadline - now));
    }
    if (!dv_ble_qn) { LeaveCriticalSection(&dv_ble_cs); return dv_text_new("tick", 4); }
    dv_text ev = dv_ble_q[dv_ble_qh];
    dv_ble_q[dv_ble_qh].p = NULL;
    dv_ble_qh = (dv_ble_qh + 1) % DV_BLE_Q;
    dv_ble_qn--;
    LeaveCriticalSection(&dv_ble_cs);
    return ev;
}
