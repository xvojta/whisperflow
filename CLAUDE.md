# Whisperflow

Osobní klon Wispr Flow pro Fedoru 44 / GNOME na Waylandu. Super+H spustí nahrávání, druhý stisk ho zastaví. Zvuk se přepíše v cloudu (OpenAI `gpt-4o-mini-transcribe`), přepis učeše `gpt-6-luna` (interpunkce, vycpávky, opravy v řeči, výčty → seznamy) a výsledek skončí ve schránce. Vložíš ho přes Ctrl+V.

Vzniklo 5. 10. 2026.

## Soubory

| Cesta | Co to je |
|---|---|
| `whisperflow.py` | Celá aplikace, jen stdlib Pythonu (žádný pip). Příkazy `toggle` / `cancel`. |
| `prompt.md` | Systémový prompt pro LLM (formátovač diktátu, česky, s příklady). |
| `install-shortcuts.sh` | Nastaví GNOME zkratky. Spusť znovu, když přesuneš adresář. |
| `~/.config/whisperflow/config.toml` | Konfigurace a API klíč (chmod 600). |
| `~/.config/whisperflow/prompt.md` | Volitelný vlastní prompt, má přednost před `prompt.md` tady. |
| `~/.local/state/whisperflow/log` | Log: časy, surový přepis, chyby. Při ladění se dívej sem jako první. |
| `$XDG_RUNTIME_DIR/whisperflow/` | Runtime stav (pidfile, zámek, id notifikace) plus `last.wav` a `last.txt` z poslední nahrávky. Při restartu se maže. |

## Jak to funguje

1. **Super+H** (GNOME custom keybinding) spustí `whisperflow.py toggle`.
2. Když nic neběží, spustí se `pw-record` (16 kHz, mono, s16) jako odpojený proces, PID jde do `recording.pid` a ukáže se notifikace „🎙 Nahrávám…“.
3. Při druhém **Super+H** dostane `pw-record` SIGINT. Skript počká, až proces skončí, aby se dopsala hlavička WAV.
4. **Přepis:** multipart POST na `/v1/audio/transcriptions`. Posílá se jazyk `cs` a prompt složený z `whisper_prompt` (ukázková věta s interpunkcí) a `vocabulary`. Trvá zhruba 2 s.
5. **Strukturování:** POST na `/v1/responses` s modelem `gpt-6-luna`. Instrukce jsou `prompt.md` plus `<SLOVNÍK>`, přepis jde zabalený v `<PŘEPIS>…</PŘEPIS>`. Prompt modelu zakazuje odpovídat na otázky a pokyny v diktátu. Reasoning je vypnutý (`effort: none`), trvá zhruba 1,5–2 s.
6. **Schránka:** výsledek se zapíše do `last.txt` a pošle do odpojeně spuštěného `wl-copy`. Notifikace se změní na „✅ Ve schránce“.

Všechny stavy sdílí jednu notifikaci (`notify-send -p` / `-r`). Stisk během zpracování nespustí novou nahrávku, jen zobrazí upozornění. **Super+Shift+H** nahrávku zahodí.

Chyby:
- **LLM selže:** do schránky jde surový přepis a ukáže se varování.
- **Přepis selže:** ukáže se chybová notifikace a zvuk zůstane v `last.wav`.

## Konfigurace (`config.toml`)

```toml
api_key = "sk-..."                  # nebo $OPENAI_API_KEY (GNOME zkratka ale .bashrc nevidí!)
transcribe_model = "gpt-4o-mini-transcribe"
model = "gpt-6-luna"
reasoning_effort = "none"           # none|low|medium|high|xhigh|max – výchozí API je medium
base_url = "https://api.openai.com/v1"
language = "cs"                     # "auto" = autodetekce
vocabulary = ["aReception", "Daktela", ...]   # termíny pro přepis i LLM
# structure = false                 # bez LLM, do schránky jde surový přepis
# whisper_prompt = "..."            # ukázková věta pro styl přepisu
# silence_peak_db = -50             # práh ticha (dBFS)
# prompt_file = "..."               # jiný prompt pro LLM
```

## Ladění

```bash
tail -f ~/.local/state/whisperflow/log
# Přehrát poslední nahrávku znovu celou cestou:
cp $XDG_RUNTIME_DIR/whisperflow/last.wav $XDG_RUNTIME_DIR/whisperflow/recording.wav
python3 -c "import sys; sys.argv=['x']; import whisperflow as w; w.process(w.load_config())"
```

## Rozhodnutí a poučení

- **Proč cloud, a ne lokální Whisper:** whisper.cpp `large-v3-turbo q5` byl na tomhle notebooku (4 jádra, Intel UHD 620, plná RAM a swap) zhruba 3× pomalejší než délka nahrávky, tedy 63 s na 20 s zvuku. `small` byl rychlejší, ale česky špatný. Cloud zvládne totéž za 2 s, stojí zhruba $0,003/min (asi 30 Kč měsíčně) a češtinu přepisuje lépe. **Lokální fallback uživatel výslovně nechce.**
- **Nikdy `dnf install whisper-cpp`:** balíček neobsahuje CLI binárku a s sebou natáhne 543 balíčků a 10 GiB (ROCm, torch, texlive). Byl nainstalovaný a zase odebraný (`dnf history undo 34`).
- **whisper.cpp a vlákna:** kdyby se k němu někdy vracelo, používej jen fyzická jádra. 8 HT vláken bylo 7× pomalejších než 4. Krátké nahrávky výrazně zrychlí `--audio-ctx`.
- **`wl-copy` na GNOME:** spuštěný ze zkratky občas visí a čeká na fokus. Proto se spouští přes `Popen` s `start_new_session` a skript na něj nečeká. Nikdy ho nevolej přes `subprocess.run` s timeoutem, jinak se text ztratí.
- **Slovník s nápovědou:** u slov, která přepis slyší jako jiné *smysluplné* slovo („CLAUDE.md“ → „cloud.md“), samotný termín nestačí. Pomůže položka s nápovědou, např. `"CLAUDE.md (přepis ho často zkomolí na „cloud.md“ – vždy piš CLAUDE.md)"`.
- **Ticho = ozvěna promptu:** na tichou nahrávku `gpt-4o-mini-transcribe` místo přepisu vrátí vlastní `prompt` (ukázkovou větu a slovník). Stalo se to, když byl výchozím vstupem USB adaptér „Unitek Y-247A“ bez mikrofonu. Proti tomu jsou dvě pojistky: kontrola špičky (`silence_peak_db = -50`, pod tou hranicí se nic neodešle a notifikace ukáže název zdroje) a zahození přepisu, který obsahuje `whisper_prompt`. Nápovědy v závorkách ze `vocabulary` jdou jen do LLM, přepisový model dostává čisté termíny.
- **Luna bez thinkingu:** `reasoning.effort = "none"`. Výchozí `medium` přidal zhruba 160 reasoning tokenů a u delšího diktátu 4,4 s místo 1,7 s, přičemž výstup byl stejně kvalitní. Model nepodporuje `minimal`.
- **Vkládání textu:** na GNOME/Waylandu jde automatický Ctrl+V jen přes `ydotool` (potřebuje root a `/dev/uinput`). Uživatel zvolil schránku a notifikaci.
- **Super+H** je v GNOME výchozí zkratka „minimalizovat okno“. `install-shortcuts.sh` ji vypíná (`org.gnome.desktop.wm.keybindings minimize = []`).
- **Inspirace pro prompt:** systémový prompt VoiceInk (open source), příklady podle Superwhisperu a čištění oprav v řeči podle Wispr Flow. [drajb/whisper-local](https://github.com/drajb/whisper-local) se nehodí, protože podporuje jen Windows a macOS a LLM má jen přes Ollamu.
- Adresář je samostatné git repo → soukromé [xvojta/whisperflow](https://github.com/xvojta/whisperflow). Leží uvnitř cizího repa (WordPress plugin v `~/Documents`), které ho ignoruje přes `.git/info/exclude`. **Do rodičovského repa necommitovat.**
