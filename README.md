# Whisperflow

Diktování do schránky, osobní klon [Wispr Flow](https://wisprflow.ai). Stiskneš zkratku a mluvíš, druhým stiskem nahrávání ukončíš. Za pár sekund máš ve schránce učesaný text s interpunkcí, bez „ehm“ a s výčty převedenými na seznamy. Vložíš ho přes Ctrl+V.

- **Přepis:** OpenAI `gpt-4o-mini-transcribe`, zhruba 2 s.
- **Úprava textu:** LLM (výchozí `gpt-6-luna`) podle [`prompt.md`](prompt.md), zhruba 2 s. Prompt je česky, jiné jazyky ale zachová.
- **Cena:** zhruba $0,003 za minutu diktátu.
- **Závislosti:** jen Python 3.11+, žádný `pip install`.

Podporované systémy jsou **Linux s GNOME na Waylandu** a **Windows 10/11**.

## Co potřebuješ

1. **OpenAI API klíč** z [platform.openai.com](https://platform.openai.com/api-keys) s nabitým kreditem.
2. **Přístup k modelu `gpt-6-luna`.** Pokud ho tvůj účet nemá, nastav v configu jiný model (třeba `model = "gpt-4.1-mini"` a `reasoning_effort = ""`). Případně dej `structure = false` a do schránky půjde surový přepis.

## Instalace – Windows

1. Nainstaluj [Python 3.11 nebo novější](https://www.python.org/downloads/windows/) a nech zaškrtnuté *py launcher* i *tcl/tk*. Obojí je výchozí.
2. Stáhni repo (`git clone` nebo *Code → Download ZIP*) do složky, kde zůstane natrvalo, např. `C:\Users\<ty>\whisperflow`.
3. V té složce spusť v PowerShellu:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```
   Skript vytvoří `%APPDATA%\whisperflow\config.toml` a otevře ho v Poznámkovém bloku. Doplň `api_key`, ulož a zavři. Pak se Whisperflow spustí a přidá se do *Po spuštění*.

**Ovládání:**

| Zkratka | Co udělá |
|---|---|
| **Ctrl+Alt+H** | Začne nahrávat, druhým stiskem nahrávání ukončí a text pošle do schránky. |
| **Ctrl+Alt+Shift+H** | Nahrávku zahodí. |

Stav ukazuje malé okénko vpravo dole. Fokus nebere, takže Ctrl+V vloží text tam, kde píšeš.

- **Jiná zkratka:** nastav `hotkey_toggle` a `hotkey_cancel` v configu. Win+H to být nemůže, protože tu Windows používá pro vlastní hlasové psaní.
- **Automatické vložení:** `paste = true` vloží výsledek rovnou do aktivního okna.
- **Změny configu** platí od další nahrávky. Jen nové zkratky vyžadují restart (`install.ps1` znovu).
- **Ukončení:** `pyw whisperflow_win.py quit`. Odinstalace: `install.ps1 -Uninstall`.
- **Log a poslední nahrávka:** `%LOCALAPPDATA%\whisperflow\` (`log.txt`, `last.wav`, `last.txt`).
- **Ladění:** spusť `py whisperflow_win.py` (s konzolí). Chyby pak uvidíš přímo.

## Instalace – Linux (GNOME, Wayland)

1. Doinstaluj nástroje pro nahrávání, schránku a notifikace (Fedora):
   ```bash
   sudo dnf install pipewire-utils wl-clipboard libnotify
   ```
   Na Ubuntu/Debianu jsou to balíčky `pipewire-bin`, `wl-clipboard` a `libnotify-bin`.
2. Naklonuj repo, připrav config a nastav zkratky:
   ```bash
   git clone https://github.com/xvojta/whisperflow.git ~/whisperflow
   mkdir -p ~/.config/whisperflow
   cp ~/whisperflow/config.example.toml ~/.config/whisperflow/config.toml
   chmod 600 ~/.config/whisperflow/config.toml
   nano ~/.config/whisperflow/config.toml        # doplň api_key
   ~/whisperflow/install-shortcuts.sh
   ```

API klíč patří do configu, ne do `.bashrc`. Zkratka GNOME proměnné z `.bashrc` nevidí.

**Ovládání:**

| Zkratka | Co udělá |
|---|---|
| **Super+H** | Začne nahrávat, druhým stiskem nahrávání ukončí a text pošle do schránky. |
| **Super+Shift+H** | Nahrávku zahodí. |

`install-shortcuts.sh` vypne výchozí GNOME zkratku Super+H, která minimalizuje okno.

- **Log:** `~/.local/state/whisperflow/log`
- **Poslední nahrávka:** `$XDG_RUNTIME_DIR/whisperflow/last.wav`

## Přizpůsobení

- **`vocabulary`** v configu: jména a termíny, které přepis komolí. U slova, které přepis slyší jako jiné smysluplné slovo, přidej nápovědu do závorky. Příklady najdeš v [`config.example.toml`](config.example.toml).
- **Vlastní prompt:** zkopíruj `prompt.md` vedle svého `config.toml` a uprav ho. Kopie má přednost před verzí v repu.
- **Tichá nahrávka se neodesílá:** když mikrofon nic nezachytí, ukáže se upozornění. Nejčastěji je jako výchozí vstup vybrané zařízení bez mikrofonu.

## Soubory

| Soubor | Co to je |
|---|---|
| `whisperflow_core.py` | Společné jádro: konfigurace, přepis, LLM. |
| `whisperflow.py` | Linux: skript spouštěný zkratkou GNOME (`toggle` / `cancel`). |
| `whisperflow_win.py` | Windows: proces na pozadí s globálními zkratkami. |
| `prompt.md` | Systémový prompt pro úpravu textu. |
| `config.example.toml` | Vzorová konfigurace. |
| `install-shortcuts.sh` / `install.ps1` | Instalace pro Linux / Windows. |
