#!/usr/bin/env python3
"""whisperflow – diktování: zkratka → nahrávání → OpenAI přepis → LLM → schránka.

Použití:
    whisperflow.py toggle   # 1. stisk start nahrávání, 2. stisk stop + zpracování
    whisperflow.py cancel   # zahodí probíhající nahrávku
"""
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import whisperflow_core as core

CONFIG_PATH = Path.home() / ".config/whisperflow/config.toml"
STATE_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "whisperflow"
LOG_PATH = Path.home() / ".local/state/whisperflow/log"
PID_FILE = STATE_DIR / "recording.pid"
LOCK_FILE = STATE_DIR / "processing.lock"
NOTIFY_ID_FILE = STATE_DIR / "notify.id"
WAV_FILE = STATE_DIR / "recording.wav"

log = logging.getLogger("whisperflow")


def load_config() -> dict:
    return core.load_config(CONFIG_PATH, os.environ.get("OPENAI_API_KEY", ""))


def notify(title: str, body: str = "", urgency: str = "normal", timeout_ms: int = 5000) -> None:
    """Jedna notifikace, kterou průběžně přepisujeme (nahrávám → zpracovávám → hotovo)."""
    cmd = ["notify-send", "-a", "Whisperflow", "-p", "-u", urgency, "-t", str(timeout_ms)]
    try:
        cmd += ["-r", NOTIFY_ID_FILE.read_text().strip()]
    except FileNotFoundError:
        pass
    cmd += [title, body]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
        if out:
            NOTIFY_ID_FILE.write_text(out)
    except Exception as e:  # notifikace nesmí shodit diktování
        log.warning("notify-send selhal: %s", e)


def recording_pid() -> int | None:
    """PID běžícího pw-record, nebo None (a uklidí zastaralý pidfile)."""
    try:
        pid = int(PID_FILE.read_text())
        if "pw-record" in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore"):
            return pid
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass
    PID_FILE.unlink(missing_ok=True)
    return None


def processing_active() -> bool:
    try:
        pid = int(LOCK_FILE.read_text())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError):
        LOCK_FILE.unlink(missing_ok=True)
        return False


def start_recording() -> None:
    WAV_FILE.unlink(missing_ok=True)
    proc = subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(WAV_FILE)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    log.info("nahrávání spuštěno, pid %s", proc.pid)
    notify("🎙 Nahrávám…", "Super+H = hotovo · Super+Shift+H = zrušit", timeout_ms=0)


def stop_recording(pid: int) -> None:
    os.kill(pid, signal.SIGINT)
    for _ in range(50):  # počkat, až pw-record dopíše hlavičku WAV
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)


def default_source_name() -> str:
    try:
        out = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"],
                             capture_output=True, text=True, timeout=2).stdout
        return re.search(r'node\.description = "([^"]+)"', out).group(1)
    except Exception:
        return "?"


def copy_to_clipboard(text: str) -> None:
    (STATE_DIR / "last.txt").write_text(text)  # záloha – text se nesmí ztratit
    # wl-copy na GNOME občas čeká na fokus; spustíme ho odpojeně a nečekáme na něj
    proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    proc.stdin.write(text.encode())
    proc.stdin.close()
    time.sleep(0.3)
    try:
        pasted = subprocess.run(["wl-paste", "-n"], capture_output=True, text=True, timeout=2).stdout
        if pasted.strip() != text.strip():
            log.warning("schránka neobsahuje nový text (wl-copy ještě nedoběhl?)")
    except subprocess.TimeoutExpired:
        log.warning("wl-paste timeout")


def process(cfg: dict) -> None:
    LOCK_FILE.write_text(str(os.getpid()))
    try:
        core.run_pipeline(cfg, WAV_FILE, notify, copy_to_clipboard, default_source_name)
    finally:
        LOCK_FILE.unlink(missing_ok=True)
        if WAV_FILE.exists():  # poslední nahrávku nechat pro ladění / porovnání modelů
            WAV_FILE.replace(STATE_DIR / "last.wav")


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    action = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    cfg = load_config()
    try:
        pid = recording_pid()
        if action == "cancel":
            if pid:
                stop_recording(pid)
                WAV_FILE.unlink(missing_ok=True)
                notify("✖ Zrušeno", timeout_ms=2000)
        elif action == "toggle":
            if pid:
                stop_recording(pid)
                process(cfg)
            elif processing_active():
                notify("⏳ Ještě zpracovávám předchozí nahrávku…", timeout_ms=2000)
            else:
                NOTIFY_ID_FILE.unlink(missing_ok=True)
                start_recording()
        else:
            sys.exit(__doc__)
    except Exception as e:
        log.exception("chyba")
        notify("❌ Whisperflow chyba", str(e)[:300], urgency="critical")
        sys.exit(1)


if __name__ == "__main__":
    main()
