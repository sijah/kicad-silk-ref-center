# Changelog — Silk Reference Tools

## v2.1.0 (2026-10-14)

### Added
- **Set height to** — set reference text to an exact height (0.1–3.0 mm),
  next to the existing **Adjust by** delta.

### Fixed
- Resizing (and fit-to-courtyard scaling) reset line thickness to height/8;
  thickness now scales with the height, keeping the text's proportions.
- Adjust-by on text already outside 0.1–3.0 mm snapped it to the limit
  (e.g. +0.10 on 4 mm text gave 3 mm). The limits now only stop a change
  from crossing them.
- Size was applied after placement, so outside placement (above/below/
  left/right) used the old size. Size now applies first.

## v2.0.1 (2026-10-14)

### Fixed
- **"BeginCommit without a document specified is not allowed when multiple
  editors are open"** on KiCad 10.0.7+ when the schematic editor (or another
  editor) is open alongside the PCB editor. The plugin now names the board
  in its commit requests. kicad-python 0.8.0 can't do this yet, so the
  field is added by the plugin; older KiCad versions ignore it.

## v2.0.0 (2026-10-14)

**Breaking: migrated from the SWIG scripting runtime to the KiCad IPC API.**
Works on KiCad 9.0 and 10.0.

SWIG-based action plugins are deprecated and stop working in KiCad 11.0. This
release replaces the in-process SWIG plugin with a standalone IPC API plugin
built on `kicad-python` (`kipy`), and renames it **Silk Reference Tools**:
one action, one settings dialog, every checked operation applied in a single
undo step.

### Added
- **Scope** filters: all footprints, selected only, or footprints matching a
  reference designator, value, or library ID (`lib:name`), the same options
  as KiCad's *Update Footprints from Library*. Case-insensitive, `*` / `?`
  wildcards. Fields are prefilled from the first selected footprint, and
  the summary reports how many footprints matched.
- **Placement**: center of courtyard, or just outside it (0.2 mm gap):
  above, below, left, right, or **Auto** (above for horizontal footprints,
  left for vertical ones). Text extents account for 90° rotation.
- **Visibility**: show or hide reference text in bulk.
- **Size adjustment**: `−` / `+` buttons or a typed mm field stage a size
  delta (0.05 mm steps, clamped 0.1–3.0 mm), applied on Run.
- **Theme** selector (System / Light / Dark). System follows Windows dark
  mode. The choice is saved per user, and changing it reopens the dialog.
  Needs wxPython 4.3+ (KiCad 10); hidden on older versions.
- Dialog and taskbar show the plugin icon instead of the Python icon.
- Plugin version shown in every dialog title.

### Changed
- Runs as a separate Python process connected to KiCad over the IPC API.
  Requires **Enable KiCad API** in Preferences → Plugins.
- `plugins/__init__.py` (SWIG registration) replaced by `plugins/plugin.json`
  + `plugins/ipc_entry.py`. `plugins/requirements.txt` declares
  `kicad-python`, which KiCad installs into a per-plugin venv.
- Dialog layout: Scope on top, then **Reference position** (side and
  placement next to rotation and text options), then **Silkscreen**
  (visibility next to size). Fits on a 768 px screen.
- Moved reference text is set to center justification, so placement is
  exact whatever the text's original justification.
- Only footprints that actually changed are sent back to KiCad, so edits
  made while the dialog is open are not overwritten.

### Fixed
- Text fit-to-courtyard accounts for text rotated ~90°.
- Skip-list CSV is written next to the board file. On an unsaved board the
  export is skipped with a message.
- A footprint without a reference field is skipped instead of aborting the
  run.
- Errors reading the board from KiCad show a message instead of failing
  silently.

### Compatibility
- KiCad 9.0 and 10.0 with the API enabled.
- The v1.0.0 SWIG release stays listed (deprecated) for KiCad 9.0 users who
  have not enabled the API. It does not work on KiCad 11.0+.

## v1.0.0 (2026-04-17)

Initial release.

### Features
- Settings dialog — configure all options before running
- Scope: all footprints or selected only
- Side: front only, back only, or both
- Rotation: match footprint rotation, always 0°, or keep existing
- Auto scale-down text to fit inside courtyard (configurable minimum size)
- Pad collision nudge — shifts text away from pad copper
- Back-side support (B.Courtyard / B.Silkscreen)
- Single undo transaction — one Ctrl+Z reverses everything
- Optional CSV export of skipped footprints
- PCM-compliant archive (v2 schema, runtime: swig)
