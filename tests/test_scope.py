"""Self-check for scope matching and auto placement.  Runs without KiCad:
wx and kipy are stubbed.  `python tests/test_scope.py`"""

import os
import sys
import types
from unittest import mock

for name in ("wx", "kipy", "kipy.board_types", "kipy.geometry",
             "kipy.proto", "kipy.proto.common", "kipy.proto.common.types",
             "kipy.util", "kipy.util.units", "kipy.proto.common.commands",
             "kipy.proto.common.commands.editor_commands_pb2",
             "google", "google.protobuf", "google.protobuf.internal",
             "google.protobuf.internal.encoder"):
    sys.modules[name] = mock.MagicMock()
sys.modules["kipy.util.units"].from_mm = lambda mm: int(mm * 1_000_000)
sys.modules["wx"].Dialog = object

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins"))
from center_silk_ref import _matches, _auto_side, run_silk_ref_tools  # noqa: E402
import center_silk_ref  # noqa: E402
center_silk_ref._with_header = lambda msg, field_no, doc: msg   # protobuf is stubbed here


def fp(ref, value, lib):
    text = lambda v: types.SimpleNamespace(text=types.SimpleNamespace(value=v))
    return types.SimpleNamespace(reference_field=text(ref), value_field=text(value),
                                 definition=types.SimpleNamespace(id=lib))


c76 = fp("C76", "100nF", "Passives:C_0402")
assert _matches(c76, "ref", "C76")
assert _matches(c76, "ref", "c7?")
assert not _matches(c76, "ref", "C7")                 # whole-string match, not prefix
assert _matches(c76, "value", "100NF")
assert _matches(c76, "lib", "*:C_0402")
assert not _matches(c76, "lib", "Passives:R_0402")

no_ref = types.SimpleNamespace(reference_field=None, value_field=None,
                               definition=types.SimpleNamespace(id="X:Y"))
assert not _matches(no_ref, "ref", "*")

# Run loop: only changed footprints are pushed back; a missing ref field is skipped
hidden = fp("C1", "1u", "L:C"); hidden.reference_field.visible = False
shown  = fp("C2", "1u", "L:C"); shown.reference_field.visible = True
board = mock.MagicMock()
board.get_footprints.return_value = [hidden, shown, no_ref]
res = run_silk_ref_tools(board, dict(scope="all", do_center=False, do_visibility=True,
                                     visible=True, do_resize=False))
assert res["visibility_changed"] == 1
board.update_items.assert_called_once_with([hidden])
assert board.client.send.call_count == 2              # begin + end commit

# Text size: stroke scales with height; limits never push text the wrong way
from center_silk_ref import _apply_size_delta, _apply_set_height  # noqa: E402
center_silk_ref.Vector2.from_xy = lambda x, y: types.SimpleNamespace(x=x, y=y)
mm = lambda v: int(v * 1_000_000)
attrs = lambda h, sw: types.SimpleNamespace(size=types.SimpleNamespace(x=h, y=h), stroke_width=sw)

a = attrs(mm(1.0), mm(0.15))
assert _apply_size_delta(a, mm(0.1)) == (True, False)
assert (a.size.y, a.size.x, a.stroke_width) == (mm(1.1), mm(1.1), mm(0.165))
big = attrs(mm(4.0), mm(0.6))
assert _apply_size_delta(big, mm(0.1)) == (False, True)       # no shrink to 3 mm
assert _apply_size_delta(big, -mm(0.5)) == (True, False) and big.size.y == mm(3.5)
tiny = attrs(mm(0.05), mm(0.01))
assert _apply_size_delta(tiny, -mm(0.05)) == (False, True)    # no grow to 0.1 mm
s = attrs(mm(2.9), mm(0.3))
assert _apply_size_delta(s, mm(0.5)) == (True, True) and s.size.y == mm(3.0)
h = attrs(mm(1.0), mm(0.2))
assert _apply_set_height(h, mm(0.8)) and h.size.y == mm(0.8) and h.stroke_width == mm(0.16)
assert not _apply_set_height(h, mm(0.8))

# Resize runs before placement, so placement sees the new size
order = []
center_silk_ref._apply_set_height = lambda a, n: order.append("size") or True
center_silk_ref._process_side = lambda *a: order.append("place") or True
c3 = fp("C3", "1u", "L:C"); c3.reference_field.text.attributes = None
c3.layer = center_silk_ref.BoardLayer.BL_F_Cu
board.get_footprints.return_value = [c3]
res = run_silk_ref_tools(board, dict(scope="all", do_center=True, process_front=True,
                                     process_back=False, do_visibility=False,
                                     do_resize=True, resize_mode="set", set_height_mm=0.8))
assert order == ["size", "place"] and res["resized"] == 1

assert _auto_side(0) == "above"
assert _auto_side(180) == "above"
assert _auto_side(-90) == "left"
assert _auto_side(270.0) == "left"
assert _auto_side(89.9) == "left"

print("ok")
