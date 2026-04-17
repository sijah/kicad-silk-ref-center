"""
center_silk_ref.py  —  Center Silk Reference in Courtyard  v1.0.0
KiCad 9.0 Action Plugin  |  Sijah AK

Moves the F/B.Silkscreen reference designator of every footprint to the
centroid of its courtyard bounding box.

Features
--------
  * Settings dialog  -- configure all options before running
  * Scope control    -- all footprints OR selected-only
  * Side filter      -- front only, back only, or both
  * Rotation control -- match footprint, always 0 deg, or keep existing
  * Text scale-down  -- auto-shrink ref to fit inside courtyard
  * Pad collision    -- nudge text away from pad copper
  * Back-side support -- handles B.CrtYd / B.SilkS correctly
  * Skip-list CSV    -- optionally export skipped components to a file
"""

import os
import csv
import pcbnew
import wx
import wx.lib.intctrl


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

NM_PER_MM       = pcbnew.FromMM(1)          # 1 000 000 nm per mm
DEFAULT_MIN_MM  = 0.4                        # minimum text height in mm
PAD_CLEARANCE   = pcbnew.FromMM(0.15)       # nudge clearance from pad edge


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _courtyard_bbox(fp: pcbnew.FOOTPRINT, layer: int):
    """
    Union bounding box of all graphic items on *layer* belonging to *fp*.
    Returns BOX2I or None.
    """
    bbox = None
    for item in fp.GraphicalItems():
        if item.GetLayer() == layer:
            ib = item.GetBoundingBox()
            if bbox is None:
                bbox = pcbnew.BOX2I(ib.GetOrigin(), ib.GetSize())
            else:
                bbox.Merge(ib)
    return bbox


def _pad_positions(fp: pcbnew.FOOTPRINT):
    """Return list of (VECTOR2I center, int half_size_nm) for all pads."""
    pads = []
    for pad in fp.Pads():
        sz  = pad.GetSize()
        hsz = max(sz.x, sz.y) // 2
        pads.append((pad.GetPosition(), hsz))
    return pads


def _nudge_clear_of_pads(cx, cy, pads, clearance):
    """
    If (cx,cy) overlaps any pad (within pad half-size + clearance),
    try eight compass candidates around the centroid at step increments
    and return the first clear position.  Falls back to original if none found.
    """
    def _overlaps(x, y):
        for (pc, hsz) in pads:
            r = hsz + clearance
            if abs(x - pc.x) < r and abs(y - pc.y) < r:
                return True
        return False

    if not _overlaps(cx, cy):
        return cx, cy

    # Try progressively larger offsets in 8 directions
    step = clearance
    for multiplier in range(1, 8):
        offset = step * multiplier
        candidates = [
            (cx,          cy - offset),   # N
            (cx + offset, cy - offset),   # NE
            (cx + offset, cy),            # E
            (cx + offset, cy + offset),   # SE
            (cx,          cy + offset),   # S
            (cx - offset, cy + offset),   # SW
            (cx - offset, cy),            # W
            (cx - offset, cy - offset),   # NW
        ]
        for (nx, ny) in candidates:
            if not _overlaps(nx, ny):
                return nx, ny

    return cx, cy     # give up — return original


def _fit_text_to_courtyard(ref, cyd_bb, min_height_nm):
    """
    If the reference text bounding box is wider or taller than the courtyard,
    scale the text height down so it fits, but never below min_height_nm.
    Returns True if the text was resized.
    """
    cyd_w = cyd_bb.GetWidth()
    cyd_h = cyd_bb.GetHeight()

    current_h  = ref.GetTextHeight()
    current_w  = ref.GetTextWidth()

    # Approximate rendered text width: char_count × 0.6 × height
    char_count = len(ref.GetShownText(False)) or 1
    approx_w   = int(char_count * 0.6 * current_h)

    # Use the tighter of the two axes
    margin = pcbnew.FromMM(0.05)
    fit_h_from_w = int((cyd_w - margin) / (char_count * 0.6)) if char_count else current_h
    fit_h_from_h = cyd_h - margin

    target_h = min(current_h, fit_h_from_w, fit_h_from_h)
    target_h = max(target_h, min_height_nm)

    if target_h < current_h:
        ratio = target_h / current_h
        ref.SetTextHeight(target_h)
        ref.SetTextWidth(max(min_height_nm, int(current_w * ratio)))
        ref.SetTextThickness(max(pcbnew.FromMM(0.05), target_h // 8))
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Settings dialog
# ─────────────────────────────────────────────────────────────────────────────

class SettingsDialog(wx.Dialog):
    """
    Pre-run settings dialog.  Stores choices as public attributes after ShowModal().
    """

    def __init__(self, parent, board):
        super().__init__(
            parent, title="Center Silk Reference — Settings",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        self.board = board

        # ── Count selected footprints for the label ──
        n_sel = sum(1 for fp in board.GetFootprints() if fp.IsSelected())
        n_all = len(list(board.GetFootprints()))

        panel = wx.Panel(self)
        vbox  = wx.BoxSizer(wx.VERTICAL)

        # ── Section: Scope ───────────────────────────────────────────────── #
        scope_box  = wx.StaticBox(panel, label="Scope")
        scope_sizer = wx.StaticBoxSizer(scope_box, wx.VERTICAL)

        self.rb_all = wx.RadioButton(panel, label=f"All footprints  ({n_all} total)", style=wx.RB_GROUP)
        self.rb_sel = wx.RadioButton(panel, label=f"Selected only  ({n_sel} selected)")
        self.rb_sel.Enable(n_sel > 0)

        scope_sizer.Add(self.rb_all, flag=wx.ALL, border=4)
        scope_sizer.Add(self.rb_sel, flag=wx.ALL, border=4)

        # ── Section: Side ────────────────────────────────────────────────── #
        side_box   = wx.StaticBox(panel, label="Board side")
        side_sizer = wx.StaticBoxSizer(side_box, wx.VERTICAL)

        self.rb_front = wx.RadioButton(panel, label="Front side (F.Courtyard / F.Silkscreen)", style=wx.RB_GROUP)
        self.rb_back  = wx.RadioButton(panel, label="Back side  (B.Courtyard / B.Silkscreen)")
        self.rb_both  = wx.RadioButton(panel, label="Both sides")
        self.rb_front.SetValue(True)

        side_sizer.Add(self.rb_front, flag=wx.ALL, border=4)
        side_sizer.Add(self.rb_back,  flag=wx.ALL, border=4)
        side_sizer.Add(self.rb_both,  flag=wx.ALL, border=4)

        # ── Section: Text options ─────────────────────────────────────────── #
        txt_box   = wx.StaticBox(panel, label="Text options")
        txt_sizer = wx.StaticBoxSizer(txt_box, wx.VERTICAL)

        self.cb_fit = wx.CheckBox(panel, label="Scale text down to fit inside courtyard")
        self.cb_fit.SetValue(True)

        min_row = wx.BoxSizer(wx.HORIZONTAL)
        min_row.Add(wx.StaticText(panel, label="Minimum text height (mm):"), flag=wx.ALIGN_CENTER_VERTICAL)
        self.sp_min = wx.SpinCtrlDouble(panel, value=str(DEFAULT_MIN_MM),
                                        min=0.1, max=2.0, inc=0.05)
        self.sp_min.SetDigits(2)
        min_row.Add(self.sp_min, flag=wx.LEFT, border=8)

        self.cb_fit.Bind(wx.EVT_CHECKBOX, self._on_fit_toggle)

        txt_sizer.Add(self.cb_fit, flag=wx.ALL, border=4)
        txt_sizer.Add(min_row,     flag=wx.ALL, border=4)

        # ── Section: Collision ────────────────────────────────────────────── #
        col_box   = wx.StaticBox(panel, label="Pad collision")
        col_sizer = wx.StaticBoxSizer(col_box, wx.VERTICAL)

        self.cb_nudge = wx.CheckBox(panel, label="Nudge text away from pad copper")
        self.cb_nudge.SetValue(True)
        col_sizer.Add(self.cb_nudge, flag=wx.ALL, border=4)


        # -- Section: Rotation ------------------------------------------------ #
        rot_box   = wx.StaticBox(panel, label="Text rotation")
        rot_sizer = wx.StaticBoxSizer(rot_box, wx.VERTICAL)

        self.rb_rot_fp   = wx.RadioButton(panel, label="Match footprint rotation", style=wx.RB_GROUP)
        self.rb_rot_zero = wx.RadioButton(panel, label="Always 0 degrees (horizontal)")
        self.rb_rot_keep = wx.RadioButton(panel, label="Keep existing rotation")
        self.rb_rot_fp.SetValue(True)

        rot_sizer.Add(self.rb_rot_fp,   flag=wx.ALL, border=4)
        rot_sizer.Add(self.rb_rot_zero, flag=wx.ALL, border=4)
        rot_sizer.Add(self.rb_rot_keep, flag=wx.ALL, border=4)

                # ── Section: Export ───────────────────────────────────────────────── #
        exp_box   = wx.StaticBox(panel, label="Skip-list export")
        exp_sizer = wx.StaticBoxSizer(exp_box, wx.VERTICAL)

        self.cb_csv = wx.CheckBox(panel, label="Export skipped footprints to CSV")
        self.cb_csv.SetValue(False)
        exp_sizer.Add(self.cb_csv, flag=wx.ALL, border=4)

        # ── Assemble ─────────────────────────────────────────────────────── #
        vbox.Add(scope_sizer, flag=wx.EXPAND | wx.ALL, border=8)
        vbox.Add(side_sizer,  flag=wx.EXPAND | wx.ALL, border=8)
        vbox.Add(txt_sizer,   flag=wx.EXPAND | wx.ALL, border=8)
        vbox.Add(col_sizer,   flag=wx.EXPAND | wx.ALL, border=8)
        vbox.Add(rot_sizer,   flag=wx.EXPAND | wx.ALL, border=8)
        vbox.Add(exp_sizer,   flag=wx.EXPAND | wx.ALL, border=8)

        btn_sizer = wx.StdDialogButtonSizer()
        btn_ok     = wx.Button(panel, wx.ID_OK,     label="Run")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, label="Cancel")
        btn_sizer.AddButton(btn_ok)
        btn_sizer.AddButton(btn_cancel)
        btn_sizer.Realize()
        btn_ok.SetDefault()
        vbox.Add(btn_sizer, flag=wx.EXPAND | wx.ALL, border=8)

        panel.SetSizer(vbox)
        vbox.Fit(self)
        self.SetMinSize(self.GetSize())
        self.Centre()

    def _on_fit_toggle(self, evt):
        self.sp_min.Enable(self.cb_fit.IsChecked())

    # ── Public property accessors ────────────────────────────────────────── #

    @property
    def selected_only(self):
        return self.rb_sel.GetValue()

    @property
    def process_front(self):
        return self.rb_front.GetValue() or self.rb_both.GetValue()

    @property
    def process_back(self):
        return self.rb_back.GetValue() or self.rb_both.GetValue()

    @property
    def fit_text(self):
        return self.cb_fit.GetValue()

    @property
    def min_text_mm(self):
        return self.sp_min.GetValue()

    @property
    def nudge_pads(self):
        return self.cb_nudge.GetValue()

    @property
    def rotation_mode(self):
        """Returns 'footprint', 'zero', or 'keep'."""
        if self.rb_rot_fp.GetValue():
            return "footprint"
        if self.rb_rot_zero.GetValue():
            return "zero"
        return "keep"

    @property
    def export_csv(self):
        return self.cb_csv.GetValue()


# ─────────────────────────────────────────────────────────────────────────────
# Core processing
# ─────────────────────────────────────────────────────────────────────────────

def _process_side(fp, silk_layer, cyd_layer, opts, result):
    """
    Process a single footprint for one side.
    Mutates result dict in-place: result['moved'], result['skipped'].
    Returns True if the footprint was moved.
    """
    ref = fp.Reference()
    if ref is None:
        result["skip_no_ref"].append(fp.GetReference())
        return False

    cyd_bb = _courtyard_bbox(fp, cyd_layer)
    if cyd_bb is None:
        result["skip_no_cyd"].append(fp.GetReference())
        return False

    # Centroid
    cx = (cyd_bb.GetLeft() + cyd_bb.GetRight())  // 2
    cy = (cyd_bb.GetTop()  + cyd_bb.GetBottom()) // 2

    # Pad collision nudge
    if opts["nudge_pads"]:
        pads = _pad_positions(fp)
        cx, cy = _nudge_clear_of_pads(cx, cy, pads, PAD_CLEARANCE)

    # Apply position
    ref.SetPosition(pcbnew.VECTOR2I(cx, cy))

    # Apply rotation based on chosen mode
    rot_mode = opts.get("rotation_mode", "zero")
    if rot_mode == "zero":
        ref.SetTextAngle(pcbnew.EDA_ANGLE(0, pcbnew.DEGREES_T))
    elif rot_mode == "footprint":
        ref.SetTextAngle(fp.GetOrientation())
    # "keep" -> leave existing angle untouched

    # Text fit
    if opts["fit_text"]:
        min_nm = pcbnew.FromMM(opts["min_text_mm"])
        _fit_text_to_courtyard(ref, cyd_bb, min_nm)

    result["moved"] += 1
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Action plugin
# ─────────────────────────────────────────────────────────────────────────────

class CenterSilkRefPlugin(pcbnew.ActionPlugin):

    def defaults(self):
        self.name             = "Center Silk Reference in Courtyard"
        self.category         = "Silkscreen"
        self.description      = (
            "Moves F.Silkscreen/B.Silkscreen references to their courtyard "
            "centroid.  Supports scope, side, text scaling, and pad nudging."
        )
        self.show_toolbar_button = True
        self.icon_file_name   = os.path.join(
            os.path.dirname(__file__), "icon_24.png"
        )

    def Run(self):
        board = pcbnew.GetBoard()
        if board is None:
            wx.MessageBox("No board is currently open.",
                          "Center Silk Reference", wx.OK | wx.ICON_ERROR)
            return

        # ── Show settings dialog ─────────────────────────────────────────── #
        dlg = SettingsDialog(None, board)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return

        opts = {
            "selected_only":  dlg.selected_only,
            "process_front":  dlg.process_front,
            "process_back":   dlg.process_back,
            "fit_text":       dlg.fit_text,
            "min_text_mm":    dlg.min_text_mm,
            "nudge_pads":     dlg.nudge_pads,
            "rotation_mode":  dlg.rotation_mode,
            "export_csv":     dlg.export_csv,
        }
        dlg.Destroy()

        # ── Collect footprints per scope ─────────────────────────────────── #
        all_fps = list(board.GetFootprints())
        if opts["selected_only"]:
            fps = [fp for fp in all_fps if fp.IsSelected()]
        else:
            fps = all_fps

        # ── Result tracking ──────────────────────────────────────────────── #
        result = {
            "moved":       0,
            "skip_no_ref": [],
            "skip_no_cyd": [],
            "resized":     0,
        }

        # ── Side → layer mapping ─────────────────────────────────────────── #
        sides = []
        if opts["process_front"]:
            sides.append((pcbnew.F_SilkS, pcbnew.F_CrtYd))
        if opts["process_back"]:
            sides.append((pcbnew.B_SilkS, pcbnew.B_CrtYd))

        # ── Process all footprints ───────────────────────────────────────── #
        # Note: all mutations within a single Run() call are automatically
        # grouped as one undo entry by KiCad's SWIG Python runtime.
        for fp in fps:
            for (silk_layer, cyd_layer) in sides:
                # Determine if footprint is on the right side
                fp_on_front = (fp.GetSide() == pcbnew.F_Cu)
                if silk_layer == pcbnew.F_SilkS and not fp_on_front:
                    continue
                if silk_layer == pcbnew.B_SilkS and fp_on_front:
                    continue

                _process_side(fp, silk_layer, cyd_layer, opts, result)

        pcbnew.Refresh()

        # ── Optional CSV export ──────────────────────────────────────────── #
        csv_path = None
        if opts["export_csv"] and (result["skip_no_ref"] or result["skip_no_cyd"]):
            board_path = board.GetFileName()
            csv_path   = os.path.splitext(board_path)[0] + "_silk_skipped.csv"
            try:
                with open(csv_path, "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["Reference", "Skip reason"])
                    for r in result["skip_no_ref"]:
                        w.writerow([r, "No reference text"])
                    for r in result["skip_no_cyd"]:
                        w.writerow([r, "No courtyard"])
            except OSError as e:
                csv_path = f"(export failed: {e})"

        # ── Summary dialog ───────────────────────────────────────────────── #
        _show_summary(result, csv_path)


# ─────────────────────────────────────────────────────────────────────────────
# Summary dialog
# ─────────────────────────────────────────────────────────────────────────────

def _show_summary(result, csv_path):
    lines = [f"Moved {result['moved']} reference(s) to courtyard centre."]

    if result["skip_no_cyd"]:
        refs   = ", ".join(result["skip_no_cyd"][:12])
        extra  = len(result["skip_no_cyd"]) - 12
        if extra > 0:
            refs += f" … (+{extra} more)"
        lines.append(
            f"\nSkipped — no courtyard ({len(result['skip_no_cyd'])}):\n  {refs}"
        )

    if result["skip_no_ref"]:
        refs   = ", ".join(result["skip_no_ref"][:12])
        extra  = len(result["skip_no_ref"]) - 12
        if extra > 0:
            refs += f" … (+{extra} more)"
        lines.append(
            f"\nSkipped — no reference text ({len(result['skip_no_ref'])}):\n  {refs}"
        )

    if csv_path:
        lines.append(f"\nSkip list exported to:\n  {csv_path}")

    wx.MessageBox(
        "\n".join(lines),
        "Center Silk Reference v1.1 — Done",
        wx.OK | wx.ICON_INFORMATION
    )
