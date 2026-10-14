"""
ipc_entry.py  —  Silk Reference Tools
KiCad IPC API plugin entrypoint.

KiCad launches this script as a separate Python process (see plugin.json)
and provides the KICAD_API_SOCKET / KICAD_API_TOKEN environment variables
used by kipy.KiCad() to connect back to the running KiCad instance.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wx
from kipy.kicad import KiCad
from kipy.proto.common.types import KiCadObjectType

from center_silk_ref import (
    __version__,
    THEMES,
    SettingsDialog,
    run_silk_ref_tools,
    export_skip_csv,
    show_summary,
)

TITLE = f"Silk Reference Tools v{__version__}"
APP_ID = "com.github.sijah.center-silk-ref"


def main():
    if sys.platform == "win32":
        # Own taskbar identity instead of grouping under pythonw.exe
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    app = wx.App(False)

    # Theme must be applied before the first window is created
    config = wx.Config("SilkReferenceTools")
    theme = None
    if hasattr(app, "SetAppearance"):                     # wxPython 4.3+
        theme = config.Read("theme", "System")
        if theme not in THEMES:
            theme = "System"
        app.SetAppearance(getattr(wx.App.Appearance, theme))

    try:
        kicad = KiCad()
        board = kicad.get_board()
        all_fps      = board.get_footprints()
        selected_fps = board.get_selection(types=[KiCadObjectType.KOT_PCB_FOOTPRINT])
    except Exception as e:
        wx.MessageBox(
            f"Could not connect to KiCad, or no PCB is currently open:\n\n{e}",
            TITLE, wx.OK | wx.ICON_ERROR
        )
        return 1

    prefill = None
    if selected_fps:
        first = selected_fps[0]
        field_text = lambda f: f.text.value if f is not None else ""
        prefill = {
            "ref":   field_text(first.reference_field),
            "value": field_text(first.value_field),
            "lib":   str(first.definition.id),
        }

    dlg = SettingsDialog(None, n_all=len(all_fps), n_sel=len(selected_fps),
                         prefill=prefill, theme=theme)
    rc = dlg.ShowModal()
    if rc == wx.ID_RETRY:                                 # theme changed: save, reopen
        config.Write("theme", dlg.theme)
        config.Flush()
        dlg.Destroy()
        subprocess.Popen([sys.executable, os.path.abspath(__file__)])
        return 0
    if rc != wx.ID_OK:
        dlg.Destroy()
        return 0

    opts = {
        "scope":           dlg.scope,
        "scope_pattern":   dlg.scope_pattern,
        "do_center":       dlg.do_center,
        "process_front":   dlg.process_front,
        "process_back":    dlg.process_back,
        "placement":       dlg.placement,
        "fit_text":        dlg.fit_text,
        "min_text_mm":     dlg.min_text_mm,
        "nudge_pads":      dlg.nudge_pads,
        "rotation_mode":   dlg.rotation_mode,
        "export_csv":      dlg.export_csv,
        "do_visibility":   dlg.do_visibility,
        "visible":         dlg.visible,
        "do_resize":       dlg.do_resize,
        "resize_delta_mm": dlg.resize_delta_mm,
    }
    dlg.Destroy()

    try:
        result = run_silk_ref_tools(board, opts)
    except Exception as e:
        wx.MessageBox(f"Silk Reference Tools failed:\n\n{e}", TITLE, wx.OK | wx.ICON_ERROR)
        return 1

    csv_path = None
    if opts["do_center"] and opts["export_csv"] and (result["skip_no_ref"] or result["skip_no_cyd"]):
        csv_path = export_skip_csv(board, result)

    show_summary(result, csv_path, opts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
