# Changelog — Center Silk Reference in Courtyard

## v1.2.0 (2026-04-17)

### New features
- **Rotation control** — choose between three modes in the settings dialog:
  - Match footprint rotation (`fp.GetOrientation()`)
  - Always 0° (horizontal)
  - Keep existing text rotation unchanged

---

## v1.1.0 (2026-04-16)

### New features
- **Settings dialog** — pre-run `wx.Dialog` lets you configure all options
  before committing any changes to the board.
- **Scope control** — process *all* footprints or *selected only*.
- **Side filter** — front side, back side, or both.
- **Text scale-down** — automatically reduces reference text height so it fits
  inside the courtyard bounding box. Minimum text height is configurable
  (default 0.4 mm).
- **Pad collision nudge** — if the courtyard centroid lands on pad copper, the
  text is shifted to the nearest clear position in 8-compass increments.
- **Back-side support** — footprints on the back of the board are handled
  correctly using `B.Courtyard` and `B.Silkscreen` layers.
- **Single undo transaction** — the entire operation is grouped as one undo
  entry, so a single Ctrl+Z reverses all moves.
- **Skip-list CSV export** — optionally writes a `.csv` file next to the
  `.kicad_pcb` listing every skipped footprint and the reason.

### Fixes
- Courtyard bounding box now built via `BOX2I.Merge()` across all courtyard
  graphic items — correctly handles multi-segment and non-rectangular courtyards.
- Guard against `fp.Reference()` returning `None`.
- Summary dialog lists up to 12 skipped references per category with "+N more".

---

## v1.0.0 (2026-04-15)

- Initial release.
- Moves `F.Silkscreen` reference of all footprints to courtyard centroid.
- Rotation reset to 0°.
- PCM-compliant archive (v2 schema, `runtime: swig`).
