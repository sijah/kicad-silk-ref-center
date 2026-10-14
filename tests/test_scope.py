"""Self-check for scope matching and auto placement.  Runs without KiCad:
wx and kipy are stubbed.  `python tests/test_scope.py`"""

import os
import sys
import types
from unittest import mock

for name in ("wx", "kipy", "kipy.board_types", "kipy.geometry",
             "kipy.proto", "kipy.proto.common", "kipy.proto.common.types",
             "kipy.util", "kipy.util.units"):
    sys.modules[name] = mock.MagicMock()
sys.modules["kipy.util.units"].from_mm = lambda mm: int(mm * 1_000_000)
sys.modules["wx"].Dialog = object

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins"))
from center_silk_ref import _matches, _auto_side, run_silk_ref_tools  # noqa: E402


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
board.push_commit.assert_called_once()

assert _auto_side(0) == "above"
assert _auto_side(180) == "above"
assert _auto_side(-90) == "left"
assert _auto_side(270.0) == "left"
assert _auto_side(89.9) == "left"

print("ok")
