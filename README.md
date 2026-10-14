# kicad-silk-ref-center

**Silk Reference Tools** — a KiCad plugin that centers or places, hides/shows, and resizes footprint silkscreen reference designators, all from one settings dialog and in a single undo step.

Works on **KiCad 9.0 and 10.0**. It runs on KiCad's **IPC API** (via [kicad-python](https://pypi.org/project/kicad-python/)) rather than the legacy SWIG scripting runtime, which KiCad removes in 11.0. See [CHANGELOG.md](CHANGELOG.md).

## Features

**Scope** (applies to everything below)
- All footprints, selected only, or footprints matching a **reference designator**, **value**, or **library ID** — case-insensitive, `*` and `?` wildcards (`C*`, `R1?`, `*:C_0402`)
- Pattern fields are prefilled from the first selected footprint

**Reference position**
- Placement: center of the courtyard, or just outside it — **auto** (above for horizontal parts, left for vertical), above, below, left or right
- Side: front only, back only, or both
- Rotation: match footprint rotation, always 0°, or keep existing
- Auto scale-down text to fit inside the courtyard (configurable minimum size)
- Pad collision nudge — shifts text away from pad copper
- Optional CSV export of skipped footprints

**Silkscreen**
- Visibility — show or hide reference text in bulk
- Size adjustment — `−`/`+` buttons or type a value into the mm field (0.05 mm steps, clamped 0.1–3.0 mm)

**Theme** — System / Light / Dark selector at the bottom of the dialog (KiCad 10). System follows Windows dark mode; changing it reopens the dialog.

To put references back at their library position, use KiCad's own **Tools → Update Footprints from Library** with *Update/reset text positions*.

## Enable the KiCad API (one-time)

Preferences → Plugins → check **Enable KiCad API**. When it's running the same page shows *Listening at ipc://…*.

If the plugin reports **"Failed to connect to KiCad: Connection refused"**, the API server isn't running — restart KiCad (close the project manager too), and check the setting above.

## Installation

### Via PCM (recommended)

1. Download `com.github.sijah.center-silk-ref-2.0.1.zip` from [Releases](https://github.com/sijah/kicad-silk-ref-center/releases)
2. KiCad → **Plugin and Content Manager** → **Install from File…** → select the zip
3. Restart KiCad if the toolbar button doesn't appear

Use 2.0.1 for KiCad 9.0 / 10.0 with the API enabled. 1.0.0 is the legacy SWIG version for KiCad 9.0 without the API.

### Manual

Copy the `plugins/` folder contents into your KiCad plugins directory (`<version>` = `9.0` or `10.0`):

| OS | Path |
|---|---|
| Windows | `C:\Users\<user>\Documents\KiCad\<version>\plugins\com_github_sijah_center-silk-ref\` |
| Linux | `~/.local/share/KiCad/<version>/plugins/com_github_sijah_center-silk-ref/` |
| macOS | `~/Documents/KiCad/<version>/plugins/com_github_sijah_center-silk-ref/` |

KiCad creates an isolated Python environment for the plugin and installs `kicad-python` from `requirements.txt` the first time it loads.

### If the toolbar button doesn't appear

The venv is built in the background on first load; the button appears only once that succeeds.

- Preferences → Plugins → **Python interpreter** should point at your current KiCad's `pythonw.exe` (e.g. `C:\Program Files\KiCad\10.0\bin\pythonw.exe` — not a leftover 9.0 path). Use **Detect Automatically**.
- KiCad 10.0.1+ shows plugin stdout/stderr in the status-bar warnings.
- The venv is at `<KiCad cache>/python-environments/<plugin identifier>`. Preferences → PCB Editor → Plugins → right-click the action → **Recreate Plugin Environment**.
- For trace output set `KICAD_ALLOC_CONSOLE=1`, `KICAD_ENABLE_WXTRACE=1`, `WXTRACE=KICAD_API` before launching KiCad.

## Migrating from 1.0.0 (SWIG)

1.0.0 ("Center Silk Reference in Courtyard") ran inside KiCad's SWIG scripting runtime, which is deprecated and removed in KiCad 11.0. 2.0.0 is a rewrite on the IPC API. Everything 1.0.0 did is still there, plus the new features above.

1. **Enable the KiCad API**: Preferences → Plugins → **Enable KiCad API** (see above). 2.0.0 can't connect without it.
2. **Remove 1.0.0**:
   - Installed via PCM: just install the 2.0.0 zip. It has the same package identifier, so PCM replaces 1.0.0.
   - Installed manually: delete the old folder from `scripting/plugins/` (e.g. `Documents\KiCad\<version>\scripting\plugins\`). Otherwise the old "Center Silk Reference in Courtyard" button still shows next to the new one on KiCad 9.
3. **Install 2.0.0** (see Installation). It goes in `plugins/`, not `scripting/plugins/`.
4. **Check the Python interpreter** in Preferences → Plugins points at your current KiCad version, then restart KiCad. The toolbar button appears once KiCad has built the plugin's Python environment.

What's different:

| | 1.0.0 | 2.0.0 |
|---|---|---|
| Runtime | SWIG, inside KiCad | IPC API, separate Python process |
| KiCad | 9.0 only | 9.0 and 10.0 (11.0-ready) |
| Needs API enabled | No | Yes |
| Install folder | `scripting/plugins/` | `plugins/` |
| Scope | All / selected | All / selected / ref / value / library ID |
| Placement | Courtyard center | Center, auto, above, below, left, right |
| Also does | — | Bulk visibility, text size, theme |

The defaults (front side, match footprint rotation, scale to fit, pad nudge) behave the same as 1.0.0, so a plain **Run** gives the same result as before. One difference: moved reference text is now set to center justification so placement is exact.

## Usage

1. Open your board in the PCB Editor
2. Click the **Silk Reference Tools** toolbar button (or Tools → External Plugins)
3. Pick a scope
4. Check the operations you want — Reference position and/or Silkscreen visibility / size
5. **Run** — everything checked runs together; one Ctrl+Z undoes it all

## Requirements

- KiCad 9.0 or 10.0 with **Enable KiCad API** turned on
- Python is provided by KiCad (a venv is created for the plugin automatically)

## Author

Sijah AK — [github.com/sijah](https://github.com/sijah)

## License

MIT © 2026 Sijah AK
