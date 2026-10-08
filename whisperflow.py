#!/usr/bin/env python3
"""whisperflow – diktování: zkratka → nahrávání → OpenAI přepis → LLM → schránka.

Použití:
    whisperflow.py toggle   # 1. stisk start nahrávání, 2. stisk stop + zpracování
    whisperflow.py cancel   # zahodí probíhající nahrávku
"""
import array
import json
import math
import re
import logging
import os
import signal
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".config/whisperflow/config.toml"
STATE_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "whisperflow"
LOG_PATH = Path.home() / ".local/state/whisperflow/log"
PID_FILE = STATE_DIR / "recording.pid"
LOCK_FILE = STATE_DIR / "processing.lock"
NOTIFY_ID_FILE = STATE_DIR / "notify.id"
WAV_FILE = STATE_DIR / "recording.wav"

DEFAULTS = {
    "transcribe_model": "gpt-4o-mini-transcribe",
    "language": "cs",
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-6-luna",
    "silence_peak_db": -50,  # tišší nahrávka = mikrofon nic nezachytil, nic se neodesílá
    "reasoning_effort": "none",  # formátování nepotřebuje přemýšlení; výchozí "medium" je pomalejší
    "timeout": 60,
    "structure": True,
    "prompt_file": "",  # prázdné = ~/.config/whisperflow/prompt.md, jinak prompt.md vedle skriptu
    # Termíny, které přepis komolí – jdou do LLM (<SLOVNÍK>) i do přepisu jako prompt
    "vocabulary": [],
    # Ukázka stylu pro přepis: věta s interpunkcí ho navede psát čárky a tečky
    "whisper_prompt": "Ahoj, posílám shrnutí. Zítra v 10:00 máme schůzku, prosím, připrav podklady.",
}

log = logging.getLogger("whisperflow")


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            cfg.update(tomllib.load(f))
    cfg["api_key"] = cfg["api_key"] or os.environ.get("OPENAI_API_KEY", "")
    candidates = [cfg["prompt_file"], CONFIG_PATH.parent / "prompt.md", Path(__file__).resolve().parent / "prompt.md"]
    cfg["prompt"] = next(Path(c).expanduser().read_text() for c in candidates if c and Path(c).expanduser().exists())
    return cfg


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


def whisper_prompt(cfg: dict) -> str:
    # Přepisový model dostane jen čisté termíny; nápovědy v závorkách jsou pro LLM
    terms = [re.sub(r"\s*\(.*\)\s*$", "", v) for v in cfg["vocabulary"]]
    return " ".join(filter(None, [cfg["whisper_prompt"], ", ".join(terms)]))


def peak_db() -> float:
    """Špička nahrávky v dBFS (WAV s16 mono z pw-record)."""
    samples = array.array("h", WAV_FILE.read_bytes()[44:])
    peak = max((abs(x) for x in samples), default=0)
    return 20 * math.log10(peak / 32768) if peak else -120.0


def default_source_name() -> str:
    try:
        out = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"],
                             capture_output=True, text=True, timeout=2).stdout
        return re.search(r'node\.description = "([^"]+)"', out).group(1)
    except Exception:
        return "?"


def transcribe(cfg: dict) -> str:
    duration = (WAV_FILE.stat().st_size - 44) / (16000 * 2)
    t0 = time.monotonic()
    text = transcribe_cloud(cfg)
    log.info("přepis %.1f s zvuku za %.1f s: %r", duration, time.monotonic() - t0, text)
    # Na ticho model občas „přepíše“ vlastní prompt – takový výstup zahodit
    if cfg["whisper_prompt"] and cfg["whisper_prompt"][:30] in text:
        log.warning("přepis je ozvěna promptu, zahazuji")
        return ""
    for junk in ("[BLANK_AUDIO]", "[ Silence ]", "(ticho)"):
        text = text.replace(junk, "")
    return text.strip()


def transcribe_cloud(cfg: dict) -> str:
    """OpenAI /audio/transcriptions – multipart ručně, ať nepotřebujeme žádné závislosti."""
    boundary = f"whisperflow{time.time_ns()}"
    fields = {"model": cfg["transcribe_model"], "response_format": "json", "prompt": whisper_prompt(cfg)}
    if cfg["language"] != "auto":
        fields["language"] = cfg["language"]
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items() if v]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + WAV_FILE.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/audio/transcriptions", data=b"".join(parts), method="POST",
        headers={"Authorization": f"Bearer {cfg['api_key']}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
            return json.load(r)["text"].strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API {e.code}: {e.read().decode(errors='ignore')[:300]}") from e


def structure(cfg: dict, text: str) -> str:
    """Pošle přepis na OpenAI Responses API a vrátí strukturovaný text."""
    instructions = cfg["prompt"] + "\n<SLOVNÍK>\n" + "\n".join(cfg["vocabulary"]) + "\n</SLOVNÍK>"
    user_input = f"<PŘEPIS>\n{text}\n</PŘEPIS>"
    body = json.dumps({"model": cfg["model"], "instructions": instructions, "input": user_input,
                       "reasoning": {"effort": cfg["reasoning_effort"]}}).encode()
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/responses", data=body, method="POST",
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API {e.code}: {e.read().decode(errors='ignore')[:300]}") from e
    out = "".join(
        c.get("text", "")
        for item in data.get("output", []) if item.get("type") == "message"
        for c in item.get("content", []) if c.get("type") == "output_text"
    ).strip()
    log.info("LLM za %.1f s", time.monotonic() - t0)
    if not out:
        raise RuntimeError(f"prázdná odpověď API: {json.dumps(data)[:300]}")
    return out


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
        level = peak_db()
        if level < cfg["silence_peak_db"]:
            source = default_source_name()
            log.warning("ticho: špička %.1f dB, zdroj %s", level, source)
            notify("🔇 Mikrofon nic nezachytil", f"Zdroj: {source} (špička {level:.0f} dB). "
                   "Zkontroluj vstup v Nastavení → Zvuk.", urgency="critical")
            return
        notify("⏳ Přepisuji…", timeout_ms=0)
        text = transcribe(cfg)
        if not text:
            notify("🤷 Nic nenahráno", "Přepis je prázdný.")
            return
        if cfg["structure"] and cfg["api_key"]:
            notify("✨ Strukturuji…", text[:200], timeout_ms=0)
            try:
                text = structure(cfg, text)
            except Exception as e:
                log.exception("LLM selhal")
                copy_to_clipboard(text)  # diktát nesmí přijít vniveč
                notify("⚠ LLM selhal – ve schránce je surový přepis", str(e)[:200], urgency="critical")
                return
        copy_to_clipboard(text)
        notify("✅ Ve schránce", text[:300])
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
