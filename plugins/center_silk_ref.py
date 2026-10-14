"""
center_silk_ref.py  —  Silk Reference Tools  v2.1.0
KiCad IPC API Plugin  |  Sijah AK

A single plugin/action that combines three operations on footprint
reference designator text (F/B.Silkscreen), applied over a shared scope
(all, selected, or matching a ref / value / library ID pattern).  Below the
scope the dialog has two sections, Reference position and Silkscreen:

  * Position   -- moves the reference to its courtyard centroid or just
    outside it (auto/above/below/left/right), with side filter, rotation
    control, text scale-down, and pad-collision nudging
  * Visibility -- show or hide the reference text
  * Size       -- +/- buttons and a typed mm field stage a size delta,
    applied to the reference text height on Run

All three are optional (checkboxes) and, when enabled, run together inside
a single KiCad commit, so one Ctrl+Z reverses everything.

Runs out-of-process against a running KiCad instance via the kicad-python
(kipy) IPC client — see ipc_entry.py for the connection/launch code.
"""

import os
import csv
import fnmatch
import wx

from kipy.board_types import BoardLayer
from kipy.geometry import Vector2
from kipy.proto.common.types import KiCadObjectType, HorizontalAlignment, VerticalAlignment
from kipy.util.units import from_mm
from kipy.proto.common.commands.editor_commands_pb2 import (
    BeginCommit, BeginCommitResponse, EndCommit, EndCommitResponse, CommitAction,
)
from kipy.proto.common.types import ItemHeader
from google.protobuf.internal.encoder import _VarintBytes


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

__version__     = "2.1.0"              # single source of truth — shown in dialog titles
DEFAULT_MIN_MM  = 0.4                  # minimum text height in mm, for courtyard fit
PAD_CLEARANCE   = from_mm(0.15)        # nudge clearance from pad edge, nm
OUTSIDE_GAP     = from_mm(0.2)         # gap between courtyard and text for above/below/left/right, nm

REF_SIZE_STEP_MM = 0.05                # size change per +/- click in the dialog, mm
REF_SIZE_MIN_MM  = 0.1                 # size adjustment floor, mm
REF_SIZE_MAX_MM  = 3.0                 # size adjustment ceiling, mm
DEFAULT_SET_HEIGHT_MM = 0.8            # initial "Set height to" value, mm

THEMES = ("System", "Light", "Dark")   # names match wx.App.Appearance members
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _courtyard_bbox(fp, cyd_layer):
    """
    Union bounding box of all graphic shapes on *cyd_layer* belonging to *fp*.
    Returns Box2 or None.
    """
    bbox = None
    for shape in fp.definition.shapes:
        if shape.layer == cyd_layer:
            sb = shape.bounding_box()
            if bbox is None:
                bbox = sb
            else:
                bbox.merge(sb)
    return bbox


def _pad_positions(fp):
    """Return list of (Vector2 center, int half_size_nm) for all pads."""
    pads = []
    for pad in fp.definition.pads:
        layer = pad.padstack.copper_layer(BoardLayer.BL_F_Cu) or \
                pad.padstack.copper_layer(BoardLayer.BL_B_Cu)
        hsz = max(layer.size.x, layer.size.y) // 2 if layer is not None else 0
        pads.append((pad.position, hsz))
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


def _fit_text_to_courtyard(attrs, value, cyd_bb, min_height_nm, angle_deg=0.0):
    """
    If the reference text is wider or taller than the courtyard, scale the
    text height down so it fits, but never below min_height_nm.

    Uses a char-count heuristic (approx_width = chars * 0.6 * height) rather
    than a live GetTextExtents()-style round trip, to avoid an extra IPC call
    per footprint.  Returns True if the text was resized.
    """
    cyd_w = cyd_bb.size.x
    cyd_h = cyd_bb.size.y
    if round(angle_deg / 90) % 2:       # text rotated ~90 deg: its width runs along the courtyard height
        cyd_w, cyd_h = cyd_h, cyd_w

    current_h = attrs.size.y
    current_w = attrs.size.x

    char_count = len(value) or 1

    margin = from_mm(0.05)
    fit_h_from_w = int((cyd_w - margin) / (char_count * 0.6)) if char_count else current_h
    fit_h_from_h = cyd_h - margin

    target_h = min(current_h, fit_h_from_w, fit_h_from_h)
    target_h = max(target_h, min_height_nm)

    if target_h < current_h:
        _resize_text(attrs, target_h)
        return True
    return False


def _resize_text(attrs, target_h):
    """Sets text height to target_h (nm), scaling width and stroke thickness
    by the same ratio so proportions and line weight are kept."""
    ratio = target_h / attrs.size.y if attrs.size.y else 1.0
    attrs.size = Vector2.from_xy(int(round(attrs.size.x * ratio)), int(target_h))
    attrs.stroke_width = int(round(attrs.stroke_width * ratio))


def _place_outside(bb, attrs, text, angle_deg, placement, gap):
    """
    Position for the reference text just outside courtyard *bb*:
    above / below / left / right (board frame, y grows downward).
    Text extents use the same 0.6*height-per-char heuristic as the fit code;
    width/height swap when the text is rotated ~90 degrees.
    """
    h = attrs.size.y
    w = int(max(len(text), 1) * 0.6 * h)
    if round(angle_deg / 90) % 2:
        w, h = h, w
    cx, cy = bb.center().x, bb.center().y
    left, top = bb.pos.x, bb.pos.y
    right, bottom = left + bb.size.x, top + bb.size.y
    if placement == "above":
        return cx, top - gap - h // 2
    if placement == "below":
        return cx, bottom + gap + h // 2
    if placement == "left":
        return left - gap - w // 2, cy
    return right + gap + w // 2, cy                     # "right"


def _auto_side(fp_angle_deg):
    """'above' for horizontal footprints (~0/180 deg), 'left' for vertical (~90/270)."""
    return "left" if round(fp_angle_deg / 90) % 2 else "above"


def _matches(fp, scope, pattern):
    """True if *fp* matches *pattern* (fnmatch, case-insensitive) for the
    'ref', 'value' or 'lib' scope."""
    if scope in ("ref", "value"):
        field = fp.reference_field if scope == "ref" else fp.value_field
        if field is None:
            return False
        subject = field.text.value
    else:                                                # "lib"
        subject = str(fp.definition.id)
    return fnmatch.fnmatch(subject.lower(), pattern.lower())


def _apply_size_delta(attrs, delta_nm):
    """
    Grows/shrinks attrs (TextAttributes) height by delta_nm, with
    proportional width and stroke thickness.  The [REF_SIZE_MIN_MM,
    REF_SIZE_MAX_MM] limits only stop the change from crossing them: text
    already outside the range is never pushed further out or snapped back.
    Returns (changed, clamped) bools.
    """
    current_h = attrs.size.y
    target_h = current_h + delta_nm
    if delta_nm > 0:
        limit = max(from_mm(REF_SIZE_MAX_MM), current_h)
        clamped = target_h > limit
        target_h = min(target_h, limit)
    else:
        limit = min(from_mm(REF_SIZE_MIN_MM), current_h)
        clamped = target_h < limit
        target_h = max(target_h, limit)

    if target_h == current_h:
        return False, clamped
    _resize_text(attrs, target_h)
    return True, clamped


def _apply_set_height(attrs, height_nm):
    """Sets text height to height_nm (proportional width/stroke).
    Returns True if it changed."""
    if attrs.size.y == height_nm:
        return False
    _resize_text(attrs, height_nm)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Settings dialog
# ─────────────────────────────────────────────────────────────────────────────

class SettingsDialog(wx.Dialog):
    """
    Single pre-run dialog covering all three operations (center, visibility,
    resize).  Stores choices as public attributes after ShowModal().
    """

    def __init__(self, parent, n_all, n_sel, prefill=None, theme=None):
        """prefill: optional {'ref', 'value', 'lib'} strings for the pattern
        fields, taken from the first selected footprint.
        theme: current THEMES entry, or None to hide the theme selector
        (wxPython < 4.3 can't switch appearance).  Picking a new theme ends
        the dialog with wx.ID_RETRY and sets self.theme."""
        super().__init__(
            parent, title=f"Silk Reference Tools — Settings (v{__version__})",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        # Own icon, so the taskbar doesn't show the Python interpreter's
        icons = wx.IconBundle()
        for name in ("icon_24.png", "icon_64.png"):
            path = os.path.join(PLUGIN_DIR, name)
            if os.path.exists(path):
                icons.AddIcon(wx.Icon(path, wx.BITMAP_TYPE_PNG))
        self.SetIcons(icons)
        self.theme = theme

        panel = wx.Panel(self)
        vbox  = wx.BoxSizer(wx.VERTICAL)

        # ── Section: Scope ───────────────────────────────────────────────── #
        scope_box  = wx.StaticBox(panel, label="Scope (applies to all operations below)")
        scope_sizer = wx.StaticBoxSizer(scope_box, wx.VERTICAL)

        self.rb_all = wx.RadioButton(panel, label=f"All footprints  ({n_all} total)", style=wx.RB_GROUP)
        self.rb_sel = wx.RadioButton(panel, label=f"Selected only  ({n_sel} selected)")
        self.rb_sel.Enable(n_sel > 0)

        scope_sizer.Add(self.rb_all, flag=wx.ALL, border=4)
        scope_sizer.Add(self.rb_sel, flag=wx.ALL, border=4)

        # Pattern scopes, like KiCad's "Update Footprints from Library" dialog
        match_grid = wx.FlexGridSizer(cols=2, vgap=4, hgap=8)
        match_grid.AddGrowableCol(1)
        self.scope_patterns = {}                         # scope key -> (radio, text ctrl)
        for key, label, hint in (("ref",   "Matching reference designator:", "C76, R1*, U?"),
                                 ("value", "Matching value:",                "100nF"),
                                 ("lib",   "With library ID:",               "Passives:C_0402")):
            rb = wx.RadioButton(panel, label=label)
            tc = wx.TextCtrl(panel, size=(200, -1))
            tc.SetHint(hint)
            tc.SetValue((prefill or {}).get(key, ""))
            rb.Bind(wx.EVT_RADIOBUTTON, self._on_scope)
            match_grid.Add(rb, flag=wx.ALIGN_CENTER_VERTICAL)
            match_grid.Add(tc, flag=wx.EXPAND)
            self.scope_patterns[key] = (rb, tc)
        self.rb_all.Bind(wx.EVT_RADIOBUTTON, self._on_scope)
        self.rb_sel.Bind(wx.EVT_RADIOBUTTON, self._on_scope)
        # Set the default only after the whole radio group exists: adding
        # radios to a group afterwards can move the selection (wxMSW).
        (self.rb_sel if n_sel > 0 else self.rb_all).SetValue(True)
        scope_sizer.Add(match_grid, flag=wx.EXPAND | wx.ALL, border=4)
        scope_sizer.Add(wx.StaticText(panel, label="Patterns are case-insensitive; * and ? wildcards allowed."),
                        flag=wx.LEFT | wx.BOTTOM, border=4)

        # Spacing: headings/top-level options flush at PAD, sub-options
        # indented by INDENT, ROW between rows, one GAP between groups.
        # Vertical space is explicit spacers (a wx border applies to every
        # flagged side, so LEFT|TOP indent would also add top space).
        PAD, INDENT, ROW, GAP = 4, 20, 3, 10

        def add_option(sizer, ctrl, indent=PAD):
            sizer.AddSpacer(ROW)
            sizer.Add(ctrl, flag=wx.LEFT, border=indent)

        def add_group(sizer, label, ctrls, first=False):
            if not first:
                sizer.AddSpacer(GAP)
            sizer.Add(wx.StaticText(panel, label=label), flag=wx.LEFT, border=PAD)
            for c in ctrls:
                add_option(sizer, c, INDENT)

        # ── Reference position: two sub-columns ──────────────────────────── #
        center_box   = wx.StaticBox(panel, label="Reference position")
        center_sizer = wx.StaticBoxSizer(center_box, wx.VERTICAL)

        self.cb_op_center = wx.CheckBox(panel, label="Move reference relative to courtyard")
        self.cb_op_center.SetValue(True)
        center_sizer.Add(self.cb_op_center, flag=wx.ALL, border=PAD)

        self.rb_front = wx.RadioButton(panel, label="Front (F.Courtyard / F.Silkscreen)", style=wx.RB_GROUP)
        self.rb_back  = wx.RadioButton(panel, label="Back  (B.Courtyard / B.Silkscreen)")
        self.rb_both  = wx.RadioButton(panel, label="Both sides")
        self.rb_front.SetValue(True)

        self.rb_pl_center = wx.RadioButton(panel, label="Center of courtyard", style=wx.RB_GROUP)
        self.rb_pl_auto   = wx.RadioButton(panel, label="Auto (above if horizontal, left if vertical)")
        self.rb_pl_above  = wx.RadioButton(panel, label="Above courtyard")
        self.rb_pl_below  = wx.RadioButton(panel, label="Below courtyard")
        self.rb_pl_left   = wx.RadioButton(panel, label="Left of courtyard")
        self.rb_pl_right  = wx.RadioButton(panel, label="Right of courtyard")
        self.rb_pl_center.SetValue(True)

        col_a = wx.BoxSizer(wx.VERTICAL)
        add_group(col_a, "Board side:", [self.rb_front, self.rb_back, self.rb_both], first=True)
        add_group(col_a, "Placement:", [self.rb_pl_center, self.rb_pl_auto, self.rb_pl_above,
                                        self.rb_pl_below, self.rb_pl_left, self.rb_pl_right])

        self.rb_rot_fp   = wx.RadioButton(panel, label="Match footprint rotation", style=wx.RB_GROUP)
        self.rb_rot_zero = wx.RadioButton(panel, label="Always 0 degrees (horizontal)")
        self.rb_rot_keep = wx.RadioButton(panel, label="Keep existing rotation")
        self.rb_rot_fp.SetValue(True)

        self.cb_fit = wx.CheckBox(panel, label="Scale text down to fit inside courtyard")
        self.cb_fit.SetValue(True)
        min_row = wx.BoxSizer(wx.HORIZONTAL)
        min_row.Add(wx.StaticText(panel, label="Minimum text height (mm):"), flag=wx.ALIGN_CENTER_VERTICAL)
        self.sp_min = wx.SpinCtrlDouble(panel, value=str(DEFAULT_MIN_MM),
                                        min=0.1, max=2.0, inc=0.05)
        self.sp_min.SetDigits(2)
        min_row.Add(self.sp_min, flag=wx.LEFT, border=8)

        self.cb_nudge = wx.CheckBox(panel, label="Nudge text away from pad copper")
        self.cb_nudge.SetValue(True)
        self.cb_csv = wx.CheckBox(panel, label="Export skipped footprints to CSV")
        self.cb_csv.SetValue(False)

        col_b = wx.BoxSizer(wx.VERTICAL)
        add_group(col_b, "Text rotation:", [self.rb_rot_fp, self.rb_rot_zero, self.rb_rot_keep], first=True)
        col_b.AddSpacer(GAP)
        add_option(col_b, self.cb_fit)
        add_option(col_b, min_row, indent=INDENT)       # belongs to the fit option
        add_option(col_b, self.cb_nudge)
        add_option(col_b, self.cb_csv)

        center_cols = wx.BoxSizer(wx.HORIZONTAL)
        center_cols.Add(col_a, flag=wx.RIGHT, border=24)
        center_cols.Add(col_b)
        center_sizer.Add(center_cols, flag=wx.ALL, border=PAD)

        # ── Silkscreen: visibility | size, side by side ──────────────────── #
        silk_box   = wx.StaticBox(panel, label="Silkscreen")
        silk_sizer = wx.StaticBoxSizer(silk_box, wx.HORIZONTAL)

        self.cb_op_visibility = wx.CheckBox(panel, label="Set reference text visibility")
        self.cb_op_visibility.SetValue(False)
        self.rb_vis_hide = wx.RadioButton(panel, label="Hide reference text", style=wx.RB_GROUP)
        self.rb_vis_show = wx.RadioButton(panel, label="Show reference text")
        self.rb_vis_hide.SetValue(True)

        vis_col = wx.BoxSizer(wx.VERTICAL)
        add_option(vis_col, self.cb_op_visibility)
        add_option(vis_col, self.rb_vis_hide, indent=INDENT)
        add_option(vis_col, self.rb_vis_show, indent=INDENT)

        self.cb_op_resize = wx.CheckBox(panel, label="Change reference text size")
        self.cb_op_resize.SetValue(False)
        self.rb_size_delta = wx.RadioButton(panel, label="Adjust by", style=wx.RB_GROUP)
        self.rb_size_set   = wx.RadioButton(panel, label="Set height to")
        self.rb_size_delta.SetValue(True)
        resize_row = wx.BoxSizer(wx.HORIZONTAL)
        resize_row.Add(self.rb_size_delta, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        self.btn_size_minus = wx.Button(panel, label="−", size=(28, -1))
        self.sp_resize_delta = wx.SpinCtrlDouble(
            panel, value="0.00",
            min=-(REF_SIZE_MAX_MM - REF_SIZE_MIN_MM), max=(REF_SIZE_MAX_MM - REF_SIZE_MIN_MM),
            inc=REF_SIZE_STEP_MM
        )
        self.sp_resize_delta.SetDigits(2)
        self.btn_size_plus  = wx.Button(panel, label="+", size=(28, -1))
        resize_row.Add(self.btn_size_minus, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        resize_row.Add(self.sp_resize_delta, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        resize_row.Add(self.btn_size_plus, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        resize_row.Add(wx.StaticText(panel, label="mm"), flag=wx.ALIGN_CENTER_VERTICAL)

        set_row = wx.BoxSizer(wx.HORIZONTAL)
        self.sp_set_height = wx.SpinCtrlDouble(
            panel, value=f"{DEFAULT_SET_HEIGHT_MM:.2f}",
            min=REF_SIZE_MIN_MM, max=REF_SIZE_MAX_MM, inc=REF_SIZE_STEP_MM
        )
        self.sp_set_height.SetDigits(2)
        set_row.Add(self.rb_size_set, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        set_row.Add(self.sp_set_height, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        set_row.Add(wx.StaticText(panel, label="mm"), flag=wx.ALIGN_CENTER_VERTICAL)

        size_col = wx.BoxSizer(wx.VERTICAL)
        add_option(size_col, self.cb_op_resize)
        add_option(size_col, resize_row, indent=INDENT)
        add_option(size_col, set_row, indent=INDENT)

        silk_sizer.Add(vis_col,  flag=wx.ALL, border=PAD)
        silk_sizer.AddSpacer(24)
        silk_sizer.Add(size_col, flag=wx.ALL, border=PAD)

        self.Bind(wx.EVT_BUTTON, self._on_size_minus, self.btn_size_minus)
        self.Bind(wx.EVT_BUTTON, self._on_size_plus, self.btn_size_plus)

        # ── Assemble: scope, reference position, silkscreen — stacked ────── #
        vbox.Add(scope_sizer,  flag=wx.EXPAND | wx.ALL, border=8)
        vbox.Add(center_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        vbox.Add(silk_sizer,   flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)

        btn_sizer = wx.StdDialogButtonSizer()
        btn_ok     = wx.Button(panel, wx.ID_OK,     label="Run")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, label="Cancel")
        btn_sizer.AddButton(btn_ok)
        btn_sizer.AddButton(btn_cancel)
        btn_sizer.Realize()
        btn_ok.SetDefault()
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        if theme is not None:
            self.ch_theme = wx.Choice(panel, choices=list(THEMES))
            self.ch_theme.SetStringSelection(theme)
            self.ch_theme.Bind(wx.EVT_CHOICE, self._on_theme)
            bottom.Add(wx.StaticText(panel, label="Theme:"), flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=4)
            bottom.Add(self.ch_theme, flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=6)
        bottom.AddStretchSpacer()
        bottom.Add(btn_sizer, flag=wx.ALIGN_CENTER_VERTICAL)
        vbox.Add(bottom, flag=wx.EXPAND | wx.ALL, border=8)

        panel.SetSizer(vbox)
        vbox.Fit(self)
        self.SetMinSize(self.GetSize())
        self.Centre()

        for cb in (self.cb_op_center, self.cb_op_visibility, self.cb_op_resize, self.cb_fit):
            cb.Bind(wx.EVT_CHECKBOX, self._on_toggle)
        for rb in (self.rb_size_delta, self.rb_size_set):
            rb.Bind(wx.EVT_RADIOBUTTON, self._on_toggle)
        for rb in (self.rb_pl_center, self.rb_pl_auto, self.rb_pl_above, self.rb_pl_below,
                   self.rb_pl_left, self.rb_pl_right):
            rb.Bind(wx.EVT_RADIOBUTTON, self._on_toggle)
        self.Bind(wx.EVT_BUTTON, self._on_run, btn_ok)

        self._refresh_enabled_state()
        self._on_scope(None)

    def _on_toggle(self, evt):
        self._refresh_enabled_state()

    def _on_theme(self, evt):
        # Appearance can't change once a window exists (wxMSW), so the
        # caller saves the choice and relaunches the plugin.
        self.theme = self.ch_theme.GetStringSelection()
        self.EndModal(wx.ID_RETRY)

    def _on_scope(self, evt):
        for rb, tc in self.scope_patterns.values():
            tc.Enable(rb.GetValue())

    def _refresh_enabled_state(self):
        center_on = self.cb_op_center.GetValue()
        vis_on    = self.cb_op_visibility.GetValue()
        resize_on = self.cb_op_resize.GetValue()

        for ctrl in (self.rb_front, self.rb_back, self.rb_both,
                     self.rb_rot_fp, self.rb_rot_zero, self.rb_rot_keep,
                     self.rb_pl_center, self.rb_pl_auto, self.rb_pl_above, self.rb_pl_below,
                     self.rb_pl_left, self.rb_pl_right, self.cb_csv):
            ctrl.Enable(center_on)
        # fit-to-courtyard and pad nudge only make sense when text stays inside
        inside = center_on and self.rb_pl_center.GetValue()
        self.cb_fit.Enable(inside)
        self.cb_nudge.Enable(inside)
        self.sp_min.Enable(inside and self.cb_fit.GetValue())

        for ctrl in (self.rb_vis_hide, self.rb_vis_show):
            ctrl.Enable(vis_on)

        for ctrl in (self.rb_size_delta, self.rb_size_set):
            ctrl.Enable(resize_on)
        delta_on = resize_on and self.rb_size_delta.GetValue()
        for ctrl in (self.btn_size_minus, self.btn_size_plus, self.sp_resize_delta):
            ctrl.Enable(delta_on)
        self.sp_set_height.Enable(resize_on and not delta_on)

    def _on_size_minus(self, evt):
        new_val = round(self.sp_resize_delta.GetValue() - REF_SIZE_STEP_MM, 2)
        new_val = max(self.sp_resize_delta.GetMin(), new_val)
        self.sp_resize_delta.SetValue(new_val)

    def _on_size_plus(self, evt):
        new_val = round(self.sp_resize_delta.GetValue() + REF_SIZE_STEP_MM, 2)
        new_val = min(self.sp_resize_delta.GetMax(), new_val)
        self.sp_resize_delta.SetValue(new_val)

    def _on_run(self, evt):
        if not (self.cb_op_center.GetValue() or self.cb_op_visibility.GetValue()
                or self.cb_op_resize.GetValue()):
            wx.MessageBox(
                "Select at least one operation (center, visibility, or size adjustment).",
                "Silk Reference Tools", wx.OK | wx.ICON_WARNING
            )
            return
        if self.scope in self.scope_patterns and not self.scope_pattern:
            wx.MessageBox("Enter a pattern for the selected scope.",
                          "Silk Reference Tools", wx.OK | wx.ICON_WARNING)
            return
        self.EndModal(wx.ID_OK)

    # ── Public property accessors ────────────────────────────────────────── #

    @property
    def scope(self):
        """Returns 'all', 'selected', 'ref', 'value' or 'lib'."""
        if self.rb_sel.GetValue():
            return "selected"
        for key, (rb, _tc) in self.scope_patterns.items():
            if rb.GetValue():
                return key
        return "all"

    @property
    def scope_pattern(self):
        entry = self.scope_patterns.get(self.scope)
        return entry[1].GetValue().strip() if entry else ""

    @property
    def do_center(self):
        return self.cb_op_center.GetValue()

    @property
    def process_front(self):
        return self.rb_front.GetValue() or self.rb_both.GetValue()

    @property
    def process_back(self):
        return self.rb_back.GetValue() or self.rb_both.GetValue()

    @property
    def placement(self):
        """Returns 'center', 'auto', 'above', 'below', 'left' or 'right'."""
        for name, rb in (("auto", self.rb_pl_auto),
                         ("above", self.rb_pl_above), ("below", self.rb_pl_below),
                         ("left", self.rb_pl_left), ("right", self.rb_pl_right)):
            if rb.GetValue():
                return name
        return "center"

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

    @property
    def do_visibility(self):
        return self.cb_op_visibility.GetValue()

    @property
    def visible(self):
        return self.rb_vis_show.GetValue()

    @property
    def do_resize(self):
        return self.cb_op_resize.GetValue()

    @property
    def resize_delta_mm(self):
        return self.sp_resize_delta.GetValue()

    @property
    def resize_mode(self):
        """Returns 'delta' or 'set'."""
        return "set" if self.rb_size_set.GetValue() else "delta"

    @property
    def set_height_mm(self):
        return self.sp_set_height.GetValue()


# ─────────────────────────────────────────────────────────────────────────────
# Core processing
# ─────────────────────────────────────────────────────────────────────────────

def _process_side(fp, cyd_layer, opts, result):
    """
    Centers a single footprint's reference text for one courtyard layer.
    Mutates result dict in-place: result['moved'], result['skip_no_ref'], etc.
    Returns True if the footprint was moved.
    """
    ref_field = fp.reference_field
    ref_text  = ref_field.text.value if ref_field is not None else ""

    if not ref_text.strip():
        result["skip_no_ref"].append(str(fp.id.value))
        return False

    cyd_bb = _courtyard_bbox(fp, cyd_layer)
    if cyd_bb is None:
        result["skip_no_cyd"].append(ref_text)
        return False

    placement = opts.get("placement", "center")
    if placement == "auto":
        placement = _auto_side(fp.orientation.degrees)

    # Rotation first: outside placement needs the final angle for extents
    rot_mode = opts.get("rotation_mode", "zero")
    attrs = ref_field.text.attributes
    if rot_mode == "zero":
        attrs.angle = 0.0
    elif rot_mode == "footprint":
        attrs.angle = fp.orientation.degrees
    # "keep" -> leave existing angle untouched
    angle = attrs.angle
    angle = float(getattr(angle, "degrees", angle))

    if placement == "center":
        center = cyd_bb.center()
        cx, cy = center.x, center.y
        if opts["nudge_pads"]:
            cx, cy = _nudge_clear_of_pads(cx, cy, _pad_positions(fp), PAD_CLEARANCE)
        if opts["fit_text"]:
            _fit_text_to_courtyard(attrs, ref_text, cyd_bb, from_mm(opts["min_text_mm"]), angle)
    else:
        cx, cy = _place_outside(cyd_bb, attrs, ref_text, angle, placement, OUTSIDE_GAP)

    # Placement math treats the position as the text's center
    attrs.horizontal_alignment = HorizontalAlignment.HA_CENTER
    attrs.vertical_alignment   = VerticalAlignment.VA_CENTER
    ref_field.text.position = Vector2.from_xy(cx, cy)

    result["moved"] += 1
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Commits that name their document
# ─────────────────────────────────────────────────────────────────────────────
# KiCad 10.0.7+ rejects BeginCommit/EndCommit without a document when more
# than one editor is open ("BeginCommit without a document specified is not
# allowed when multiple editors are open").  kicad-python <= 0.8.0 has no
# header field on those messages, so append it as a raw protobuf field
# (BeginCommit.header = 1, EndCommit.header = 4); older KiCad ignores it.
# ponytail: drop this block and use board.begin_commit()/push_commit()/
# drop_commit() once requirements pin a kicad-python release that sets header.

_COMMIT_HAS_HEADER = "header" in BeginCommit.DESCRIPTOR.fields_by_name


def _with_header(msg, field_no, doc):
    if _COMMIT_HAS_HEADER:
        msg.header.document.CopyFrom(doc)
        return msg
    hdr = ItemHeader()
    hdr.document.CopyFrom(doc)
    raw = hdr.SerializeToString()
    return type(msg).FromString(msg.SerializeToString() + _VarintBytes((field_no << 3) | 2)
                                + _VarintBytes(len(raw)) + raw)


def _begin_commit(board):
    cmd = _with_header(BeginCommit(), 1, board.document)
    return board.client.send(cmd, BeginCommitResponse).id


def _end_commit(board, commit_id, action, message=""):
    cmd = EndCommit()
    cmd.id.CopyFrom(commit_id)
    cmd.action = action
    cmd.message = message
    board.client.send(_with_header(cmd, 4, board.document), EndCommitResponse)


def run_silk_ref_tools(board, opts):
    """
    Runs the enabled operations (center / visibility / resize) over `board`
    according to `opts`, sharing a single scope and a single KiCad commit —
    one Ctrl+Z reverses everything.

    opts keys: scope, scope_pattern, do_center, process_front, process_back,
    placement, fit_text, min_text_mm, nudge_pads, rotation_mode, export_csv,
    do_visibility, visible, do_resize, resize_mode ('delta' | 'set'),
    resize_delta_mm, set_height_mm.
    """
    scope = opts.get("scope", "all")
    if scope == "selected":
        fps = list(board.get_selection(types=[KiCadObjectType.KOT_PCB_FOOTPRINT]))
    else:
        fps = list(board.get_footprints())
        if scope != "all":
            fps = [fp for fp in fps if _matches(fp, scope, opts["scope_pattern"])]

    result = {
        "matched":            len(fps),
        "moved":              0,
        "skip_no_ref":        [],
        "skip_no_cyd":        [],
        "visibility_changed": 0,
        "resized":            0,
        "resize_clamped":     0,
    }

    cyd_layers = []
    if opts["do_center"]:
        if opts["process_front"]:
            cyd_layers.append(BoardLayer.BL_F_CrtYd)
        if opts["process_back"]:
            cyd_layers.append(BoardLayer.BL_B_CrtYd)

    resize_mode     = opts.get("resize_mode", "delta")
    resize_delta_nm = from_mm(opts.get("resize_delta_mm", 0.0))
    set_height_nm   = from_mm(opts.get("set_height_mm", DEFAULT_SET_HEIGHT_MM))

    commit = _begin_commit(board)
    try:
        changed = []
        for fp in fps:
            touched = False
            ref_field = fp.reference_field

            # Resize first so outside placement uses the final text size
            if ref_field is not None and opts.get("do_resize"):
                attrs = ref_field.text.attributes
                clamped = False
                if resize_mode == "set":
                    resized = _apply_set_height(attrs, set_height_nm)
                elif resize_delta_nm:
                    resized, clamped = _apply_size_delta(attrs, resize_delta_nm)
                else:
                    resized = False
                if resized:
                    touched = True
                    result["resized"] += 1
                    result["resize_clamped"] += clamped

            if opts["do_center"]:
                fp_on_front = fp.layer == BoardLayer.BL_F_Cu
                for cyd_layer in cyd_layers:
                    if cyd_layer == BoardLayer.BL_F_CrtYd and not fp_on_front:
                        continue
                    if cyd_layer == BoardLayer.BL_B_CrtYd and fp_on_front:
                        continue
                    touched |= _process_side(fp, cyd_layer, opts, result)

            if ref_field is not None and opts["do_visibility"] \
                    and ref_field.visible != opts["visible"]:
                ref_field.visible = opts["visible"]
                result["visibility_changed"] += 1
                touched = True

            if touched:
                changed.append(fp)

        # Only push back what changed: unchanged copies would overwrite any
        # edits made in KiCad while the dialog was open.
        if changed:
            board.update_items(changed)
        _end_commit(board, commit, CommitAction.CMA_COMMIT, "Silk reference tools")
    except Exception:
        _end_commit(board, commit, CommitAction.CMA_DROP)
        raise

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CSV export + summary dialog
# ─────────────────────────────────────────────────────────────────────────────

def export_skip_csv(board, result):
    """Writes the skip list next to the board file.  Returns the path, or an
    error string if the write failed."""
    # board.name is the bare filename; the project path holds the directory
    proj_dir = board.get_project().path or ""
    if os.path.isfile(proj_dir):
        proj_dir = os.path.dirname(proj_dir)
    if not proj_dir or not board.name:
        return "(export skipped: save the board first)"
    board_path = os.path.join(proj_dir, board.name)
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
        return f"(export failed: {e})"
    return csv_path


def show_summary(result, csv_path, opts):
    lines = []

    if opts.get("scope") in ("ref", "value", "lib"):
        lines.append(f"{result['matched']} footprint(s) matched '{opts['scope_pattern']}'.")

    if opts["do_center"]:
        center_lines = [f"Moved {result['moved']} reference(s) ({opts.get('placement', 'center')})."]

        if result["skip_no_cyd"]:
            refs   = ", ".join(result["skip_no_cyd"][:12])
            extra  = len(result["skip_no_cyd"]) - 12
            if extra > 0:
                refs += f" … (+{extra} more)"
            center_lines.append(f"Skipped — no courtyard ({len(result['skip_no_cyd'])}): {refs}")

        if result["skip_no_ref"]:
            refs   = ", ".join(result["skip_no_ref"][:12])
            extra  = len(result["skip_no_ref"]) - 12
            if extra > 0:
                refs += f" … (+{extra} more)"
            center_lines.append(f"Skipped — no reference text ({len(result['skip_no_ref'])}): {refs}")

        if csv_path:
            center_lines.append(f"Skip list exported to: {csv_path}")

        lines.append("\n".join(center_lines))

    if opts["do_visibility"]:
        action = "Shown" if opts["visible"] else "Hidden"
        lines.append(f"{action} {result['visibility_changed']} reference(s).")

    if opts["do_resize"]:
        how = (f"to {opts['set_height_mm']:.2f} mm" if opts.get("resize_mode") == "set"
               else f"by {opts['resize_delta_mm']:+.2f} mm")
        if result["resized"] == 0:
            lines.append(f"Size ({how}): no change — already that size, or at the size limit.")
        else:
            extra = f" ({result['resize_clamped']} hit the size limit)" if result["resize_clamped"] else ""
            lines.append(f"Resized {result['resized']} reference(s) {how}{extra}.")

    if not lines:
        lines.append("No operations selected.")

    wx.MessageBox(
        "\n\n".join(lines),
        f"Silk Reference Tools v{__version__} — Done",
        wx.OK | wx.ICON_INFORMATION
    )
