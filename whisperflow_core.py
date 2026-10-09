"""Společné jádro Whisperflow pro Linux i Windows: konfigurace, přepis, LLM.

Jen stdlib Pythonu (≥ 3.11 kvůli tomllib). Platformní části (nahrávání, schránka,
notifikace, zkratky) jsou ve whisperflow.py (Linux) a whisperflow_win.py (Windows).
"""
import array
import json
import logging
import math
import re
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

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
    "prompt_file": "",  # prázdné = prompt.md vedle config.toml, jinak prompt.md vedle skriptu
    # Termíny, které přepis komolí – jdou do LLM (<SLOVNÍK>) i do přepisu jako prompt
    "vocabulary": [],
    # Ukázka stylu pro přepis: věta s interpunkcí ho navede psát čárky a tečky
    "whisper_prompt": "Ahoj, posílám shrnutí. Zítra v 10:00 máme schůzku, prosím, připrav podklady.",
}

log = logging.getLogger("whisperflow")


def load_config(config_path: Path, env_key: str = "", extra_defaults: dict | None = None) -> dict:
    cfg = dict(DEFAULTS, **(extra_defaults or {}))
    if config_path.exists():
        # utf-8-sig: Poznámkový blok umí uložit s BOM, který tomllib neskousne
        cfg.update(tomllib.loads(config_path.read_text(encoding="utf-8-sig")))
    cfg["api_key"] = cfg["api_key"] or env_key
    candidates = [cfg["prompt_file"], config_path.parent / "prompt.md", Path(__file__).resolve().parent / "prompt.md"]
    cfg["prompt"] = next(Path(c).expanduser().read_text(encoding="utf-8-sig")
                         for c in candidates if c and Path(c).expanduser().exists())
    return cfg


def wav_pcm(wav: Path) -> bytes:
    """Vzorky z WAV (s16 mono). Hlavička nemusí mít 44 B (Windows MCI píše jinou), hledáme chunk „data“."""
    buf = wav.read_bytes()
    i = buf.find(b"data", 12)
    return buf[i + 8:] if i >= 0 else buf[44:]


def peak_db(wav: Path) -> float:
    """Špička nahrávky v dBFS."""
    pcm = wav_pcm(wav)
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    peak = max((abs(x) for x in samples), default=0)
    return 20 * math.log10(peak / 32768) if peak else -120.0


def whisper_prompt(cfg: dict) -> str:
    # Přepisový model dostane jen čisté termíny; nápovědy v závorkách jsou pro LLM
    terms = [re.sub(r"\s*\(.*\)\s*$", "", v) for v in cfg["vocabulary"]]
    return " ".join(filter(None, [cfg["whisper_prompt"], ", ".join(terms)]))


def transcribe(cfg: dict, wav: Path) -> str:
    duration = len(wav_pcm(wav)) / (16000 * 2)
    t0 = time.monotonic()
    text = transcribe_cloud(cfg, wav)
    log.info("přepis %.1f s zvuku za %.1f s: %r", duration, time.monotonic() - t0, text)
    # Na ticho model občas „přepíše“ vlastní prompt – takový výstup zahodit
    if cfg["whisper_prompt"] and cfg["whisper_prompt"][:30] in text:
        log.warning("přepis je ozvěna promptu, zahazuji")
        return ""
    for junk in ("[BLANK_AUDIO]", "[ Silence ]", "(ticho)"):
        text = text.replace(junk, "")
    return text.strip()


def transcribe_cloud(cfg: dict, wav: Path) -> str:
    """OpenAI /audio/transcriptions – multipart ručně, ať nepotřebujeme žádné závislosti."""
    boundary = f"whisperflow{time.time_ns()}"
    fields = {"model": cfg["transcribe_model"], "response_format": "json", "prompt": whisper_prompt(cfg)}
    if cfg["language"] != "auto":
        fields["language"] = cfg["language"]
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items() if v]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + wav.read_bytes() + b"\r\n")
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
    payload = {"model": cfg["model"], "instructions": instructions, "input": user_input}
    if cfg["reasoning_effort"]:  # prázdné = neposílat (modely bez reasoningu parametr odmítnou)
        payload["reasoning"] = {"effort": cfg["reasoning_effort"]}
    body = json.dumps(payload).encode()
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


def run_pipeline(cfg: dict, wav: Path, notify, copy, source_name) -> None:
    """Ticho → přepis → LLM → schránka. notify(title, body="", urgency=, timeout_ms=), copy(text) → False při selhání, source_name()."""
    level = peak_db(wav)
    if level < cfg["silence_peak_db"]:
        source = source_name()
        log.warning("ticho: špička %.1f dB, zdroj %s", level, source)
        notify("🔇 Mikrofon nic nezachytil", f"Zdroj: {source} (špička {level:.0f} dB). "
               "Zkontroluj vstup v nastavení zvuku.", urgency="critical")
        return
    notify("⏳ Přepisuji…", timeout_ms=0)
    text = transcribe(cfg, wav)
    if not text:
        notify("🤷 Nic nenahráno", "Přepis je prázdný.")
        return
    if cfg["structure"] and cfg["api_key"]:
        notify("✨ Strukturuji…", text[:200], timeout_ms=0)
        try:
            text = structure(cfg, text)
        except Exception as e:
            log.exception("LLM selhal")
            copy(text)  # diktát nesmí přijít vniveč
            notify("⚠ LLM selhal – ve schránce je surový přepis", str(e)[:200], urgency="critical")
            return
    if copy(text) is False:  # Linux vrací výsledek ověření, Windows None
        notify("⚠ Schránka selhala – text je v last.txt", text[:300], urgency="critical")
        return
    notify("✅ Ve schránce", text[:300])
