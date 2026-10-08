"""whisperflow pro Windows – zkratka → nahrávání → OpenAI přepis → LLM → schránka.

Na rozdíl od Linuxu (skript spouštěný zkratkou GNOME) tu běží jeden proces na pozadí:
zaregistruje globální zkratky (RegisterHotKey), nahrává přes MCI (winmm) a stav ukazuje
v malém okénku vpravo dole, které nekrade fokus. Jen stdlib Pythonu (≥ 3.11) + tkinter.

Použití:
    pythonw whisperflow_win.py        # spustí na pozadí (install.ps1 ho dá do Po spuštění)
    python  whisperflow_win.py        # totéž, ale s výpisem do konzole (ladění)
    python  whisperflow_win.py quit   # ukončí běžící instanci
"""
import ctypes
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

import whisperflow_core as core

if sys.platform != "win32":
    sys.exit("whisperflow_win.py je jen pro Windows; na Linuxu použij whisperflow.py")

CONFIG_PATH = Path(os.environ["APPDATA"]) / "whisperflow" / "config.toml"
STATE_DIR = Path(os.environ["LOCALAPPDATA"]) / "whisperflow"
LOG_PATH = STATE_DIR / "log.txt"
WAV_FILE = STATE_DIR / "recording.wav"

WIN_DEFAULTS = {
    # Win+H má Windows pro hlasové psaní, proto jiná výchozí zkratka. Pozor: Ctrl+Alt = AltGr
    # na české klávesnici; AltGr+H nic nepíše, ale kdo chce jinou, přepíše si ji v config.toml.
    "hotkey_toggle": "ctrl+alt+h",
    "hotkey_cancel": "ctrl+alt+shift+h",
    "paste": False,  # true = po zkopírování rovnou pošle Ctrl+V do aktivního okna
}

log = logging.getLogger("whisperflow")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")

# 64bitový Python: bez argtypes/restype by se handly ořízly na 32 bitů
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
winmm.mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
winmm.mciSendStringW.restype = wintypes.DWORD
winmm.mciGetErrorStringW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.UINT]

WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x2
ERROR_ALREADY_EXISTS = 183
GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = -20, 0x08000000, 0x80, 0x8
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
QUIT_EVENT_NAME = "Local\\WhisperflowQuit"


# ---------- nahrávání (MCI) – volat jen z hlavního vlákna ----------

def mci(cmd: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    err = winmm.mciSendStringW(cmd, buf, 256, None)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        winmm.mciGetErrorStringW(err, msg, 256)
        raise RuntimeError(f"MCI „{cmd}“: {msg.value} ({err})")
    return buf.value


def start_recording() -> None:
    WAV_FILE.unlink(missing_ok=True)
    mci("open new type waveaudio alias wf")
    try:
        # Bez tohohle MCI nahrává 8 bit / 11 kHz; jedním příkazem, jinak některé ovladače hlásí chybu
        mci("set wf bitspersample 16 channels 1 samplespersec 16000 bytespersec 32000 alignment 2")
        mci("record wf")
    except Exception:
        mci("close wf")
        raise
    log.info("nahrávání spuštěno")


def stop_recording(save: bool) -> None:
    try:
        mci("stop wf")
        if save:
            mci(f'save wf "{WAV_FILE}"')
    finally:
        mci("close wf")


# ---------- schránka a vložení ----------

def copy_to_clipboard(text: str) -> None:
    (STATE_DIR / "last.txt").write_text(text, encoding="utf-8")  # záloha – text se nesmí ztratit
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le") + b"\0\0"
    for _ in range(20):  # schránku může mít zrovna otevřenou jiná aplikace
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("schránka je zamčená jinou aplikací (text je v last.txt)")
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ctypes.memmove(kernel32.GlobalLock(h), data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        user32.CloseClipboard()


def send_ctrl_v() -> None:
    VK_CONTROL, VK_V, KEYUP = 0x11, 0x56, 0x2
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)


# ---------- zkratky ----------

def parse_hotkey(spec: str) -> tuple[int, int]:
    *mods, key = [p.strip().lower() for p in spec.split("+")]
    m = MOD_NOREPEAT
    for p in mods:
        if p not in MODS:
            raise ValueError(f"neznámý modifikátor „{p}“ ve zkratce „{spec}“ (alt/ctrl/shift/win)")
        m |= MODS[p]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key == "space":
        vk = 0x20
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"neznámá klávesa „{key}“ ve zkratce „{spec}“ (písmeno, číslice, space, F1–F24)")
    return m, vk


def hotkey_thread(bindings: dict[str, str], events: queue.Queue, ready: queue.Queue) -> None:
    """RegisterHotKey + smyčka zpráv; zkratky patří vláknu, které je zaregistrovalo."""
    ready.put(kernel32.GetCurrentThreadId())
    names = list(bindings)
    for i, name in enumerate(names, 1):
        spec = bindings[name]
        try:
            mods, vk = parse_hotkey(spec)
        except ValueError as e:
            events.put(("error", str(e)))
            continue
        if not user32.RegisterHotKey(None, i, mods, vk):
            events.put(("error", f"Zkratku {spec} už používá jiná aplikace (nebo už Whisperflow běží). "
                                 f"Změň hotkey_{name} v {CONFIG_PATH}."))
        else:
            log.info("zkratka %s = %s", name, spec)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and 1 <= msg.wParam <= len(names):
            events.put((names[msg.wParam - 1], None))


# ---------- okénko se stavem ----------

class Overlay:
    """Malé okno vpravo dole. Nikdy nebere fokus, jinak by Ctrl+V skončilo v něm."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#202124")
        self.title = tk.Label(root, bg="#202124", fg="#ffffff", font=("Segoe UI", 11, "bold"),
                              anchor="w", justify="left")
        self.body = tk.Label(root, bg="#202124", fg="#bdc1c6", font=("Segoe UI", 9),
                             anchor="w", justify="left", wraplength=360)
        self.title.pack(fill="x", padx=14, pady=(10, 0))
        self.body.pack(fill="x", padx=14, pady=(2, 10))
        root.update()  # nechat Tk okno namapovat, jinak by ho později samo ukázalo
        self.hwnd = user32.GetParent(root.winfo_id())
        style = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE,
                                 style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        user32.ShowWindow(self.hwnd, SW_HIDE)
        self.hide_job = None

    def show(self, title: str, body: str = "", urgency: str = "normal", timeout_ms: int = 5000) -> None:
        self.title.config(text=title, fg="#f28b82" if urgency == "critical" else "#ffffff")
        self.body.config(text=body)
        self.body.pack_configure(pady=(2, 10) if body else (0, 4))
        self.root.update_idletasks()
        w, h = max(self.root.winfo_reqwidth(), 260), self.root.winfo_reqheight()
        x = self.root.winfo_screenwidth() - w - 24
        y = self.root.winfo_screenheight() - h - 72  # nad hlavním panelem
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if timeout_ms > 0:
            self.hide_job = self.root.after(timeout_ms, self.hide)

    def hide(self) -> None:
        self.hide_job = None
        user32.ShowWindow(self.hwnd, SW_HIDE)


# ---------- aplikace ----------

class App:
    def __init__(self, cfg: dict, quit_event):
        self.cfg = cfg
        self.quit_event = quit_event
        self.events: queue.Queue = queue.Queue()
        self.recording = False
        self.worker: threading.Thread | None = None
        self.root = tk.Tk()
        self.root.title("Whisperflow")
        self.overlay = Overlay(self.root)
        ready: queue.Queue = queue.Queue()
        bindings = {"toggle": cfg["hotkey_toggle"], "cancel": cfg["hotkey_cancel"]}
        threading.Thread(target=hotkey_thread, args=(bindings, self.events, ready), daemon=True).start()
        self.hotkey_tid = ready.get()
        if not cfg["api_key"]:
            self.events.put(("error", f"Chybí api_key v {CONFIG_PATH}"))
        else:
            self.events.put(("notify", (("🎙 Whisperflow běží", f"{cfg['hotkey_toggle']} = nahrávat / hotovo"),
                                        {"timeout_ms": 3000})))
        self.root.after(50, self.poll)

    def notify(self, title: str, body: str = "", urgency: str = "normal", timeout_ms: int = 5000) -> None:
        """Volatelné z libovolného vlákna – tkinter se smí dotknout jen hlavní vlákno."""
        self.events.put(("notify", ((title, body), {"urgency": urgency, "timeout_ms": timeout_ms})))

    def poll(self) -> None:
        if kernel32.WaitForSingleObject(self.quit_event, 0) == 0:
            self.quit()
            return
        try:
            while True:
                kind, arg = self.events.get_nowait()
                try:
                    self.handle(kind, arg)
                except Exception as e:
                    log.exception("chyba")
                    self.overlay.show("❌ Whisperflow chyba", str(e)[:300], urgency="critical", timeout_ms=10000)
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def handle(self, kind: str, arg) -> None:
        if kind == "notify":
            args, kwargs = arg
            self.overlay.show(*args, **kwargs)
        elif kind == "error":
            log.error(arg)
            self.overlay.show("❌ Whisperflow", arg, urgency="critical", timeout_ms=15000)
        elif kind == "cancel":
            if self.recording:
                self.recording = False
                stop_recording(save=False)
                self.overlay.show("✖ Zrušeno", timeout_ms=2000)
        elif kind == "toggle":
            if self.recording:
                self.recording = False
                stop_recording(save=True)
                self.worker = threading.Thread(target=self.process, daemon=True)
                self.worker.start()
            elif self.worker and self.worker.is_alive():
                self.overlay.show("⏳ Ještě zpracovávám předchozí nahrávku…", timeout_ms=2000)
            else:
                start_recording()
                self.recording = True
                self.overlay.show("🎙 Nahrávám…", f"{self.cfg['hotkey_toggle']} = hotovo · "
                                  f"{self.cfg['hotkey_cancel']} = zrušit", timeout_ms=0)

    def process(self) -> None:
        def copy(text: str) -> None:
            copy_to_clipboard(text)
            if self.cfg["paste"]:
                time.sleep(0.1)
                send_ctrl_v()
        try:
            core.run_pipeline(self.cfg, WAV_FILE, self.notify, copy,
                              lambda: "výchozí vstup (Nastavení → Systém → Zvuk → Vstup)")
        except Exception as e:
            log.exception("chyba")
            self.notify("❌ Whisperflow chyba", str(e)[:300], urgency="critical", timeout_ms=10000)
        finally:
            if WAV_FILE.exists():  # poslední nahrávku nechat pro ladění
                WAV_FILE.replace(STATE_DIR / "last.wav")

    def quit(self) -> None:
        log.info("konec")
        if self.recording:
            stop_recording(save=False)
        user32.PostThreadMessageW(self.hotkey_tid, WM_QUIT, 0, 0)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stderr:  # pythonw nemá konzoli
        handlers.append(logging.StreamHandler())
    logging.basicConfig(handlers=handlers, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    quit_event = kernel32.CreateEventW(None, True, False, QUIT_EVENT_NAME)
    if len(sys.argv) > 1 and sys.argv[1] == "quit":
        kernel32.SetEvent(quit_event)
        return
    ctypes.set_last_error(0)
    kernel32.CreateMutexW(None, False, "Local\\WhisperflowInstance")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        log.info("už běží, končím")
        return

    try:
        cfg = core.load_config(CONFIG_PATH, os.environ.get("OPENAI_API_KEY", ""), WIN_DEFAULTS)
    except Exception as e:
        log.exception("konfigurace")
        ctypes.windll.user32.MessageBoxW(None, f"Nejde načíst {CONFIG_PATH}:\n{e}", "Whisperflow", 0x10)
        return
    log.info("start")
    App(cfg, quit_event).run()


if __name__ == "__main__":
    main()
