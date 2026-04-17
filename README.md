# kicad-center-silk-ref

KiCad 9.0 action plugin — moves every footprint's silkscreen reference designator to the centroid of its courtyard bounding box.

## Features

- Settings dialog before each run — configure everything before touching the board
- Scope: all footprints or selected only
- Side: front only, back only, or both
- Rotation: match footprint rotation, always 0°, or keep existing
- Auto scale-down text to fit inside courtyard (configurable minimum size)
- Pad collision nudge — shifts text away from pad copper automatically
- Back-side support (B.Courtyard / B.Silkscreen)
- Single undo transaction — one Ctrl+Z reverses everything
- Optional CSV export of skipped footprints

## Installation via PCM (recommended)

1. Open KiCad PCB Editor
2. Tools → Plugin and Content Manager
3. Install from File → select `com.aptener.center-silk-ref-x.x.x.zip`
4. Restart KiCad or reload plugins

## Manual installation

Copy the entire `plugins/` folder contents into your KiCad scripting plugins directory:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\kicad\9.0\scripting\plugins\com_aptener_center-silk-ref\` |
| Linux | `~/.local/share/kicad/9.0/scripting/plugins/com_aptener_center-silk-ref\` |
| macOS | `~/Library/Preferences/kicad/9.0/scripting/plugins/com_aptener_center-silk-ref\` |

Then in KiCad PCB Editor, reload plugins:

```
Tools → Scripting Console → import pcbnew; pcbnew.LoadPlugins()
```

## Usage

1. Open your `.kicad_pcb` file
2. Tools → External Plugins → Center Silk Reference in Courtyard
3. Configure options in the settings dialog
4. Click Run

## Requirements

- KiCad 9.0 or later
- Python 3.x (bundled with KiCad)

## License

MIT © 2026 AptEner Mechatronics
