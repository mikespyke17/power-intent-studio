"""ContinuousAssign extraction (VHDL) -- the new combinational-logic
primitive backend/elaborator/rtl/ir/flatten.py's own connectivity-
flattening engine needs to distinguish TRUE pass-through wiring
(`source` set) from real combinational logic (`source=None`, a
stopping point). See ContinuousAssign's own docstring, and
vhdl/elaborate.py's own _build_continuous_assigns() docstring, for the
full "why".

UNVERIFIED IN THIS ENVIRONMENT: ghdl/yosys are not installed here (see
tests/backend/test_vhdl_elaborate.py's own skip guard, reused
identically below) -- this test is written and reviewed against the
documented Yosys JSON shape (confirmed via this project's own spike
investigation into _build_bit_name_map/_resolve_bits_to_name's
existing behavior), but has not actually been executed against a real
GHDL/yosys run. Run this file in an environment with both installed
(e.g. OSS CAD Suite) to confirm it passes before trusting it fully.
"""

import shutil
from pathlib import Path

import pytest

from backend.elaborator.rtl.ir import ContinuousAssign
from backend.elaborator.rtl.vhdl import elaborate

pytestmark = pytest.mark.skipif(
    shutil.which("ghdl") is None or shutil.which("yosys") is None,
    reason="ghdl/yosys not on PATH -- see docs/milestone1_rtl_elaborator.md for setup (OSS CAD Suite or equivalent)",
)

PASSTHROUGH_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_passthrough_wire"


def test_plain_wire_assignment_is_classified_as_pass_through():
    design = elaborate([str(PASSTHROUGH_DIR / "sub.vhd")], top="sub")
    sub = design.definition(design.top)

    by_target = {ca.target: ca for ca in sub.continuous_assigns}
    assert by_target["y_passthrough"] == ContinuousAssign(target="y_passthrough", source="a")


def test_inverted_assignment_is_real_logic_not_pass_through():
    design = elaborate([str(PASSTHROUGH_DIR / "sub.vhd")], top="sub")
    sub = design.definition(design.top)

    by_target = {ca.target: ca for ca in sub.continuous_assigns}
    assert by_target["y_inverted"] == ContinuousAssign(target="y_inverted", source=None)


def test_flop_output_is_real_logic_not_undriven():
    """A register's own Q output must ALSO get a ContinuousAssign(source=None)
    entry -- omitting it would make flatten.py's own trace_driver()
    wrongly report UNDRIVEN for a signal that's actually driven by a
    real flop, since a Yosys $dff primitive is never a `user Instance`
    the way a real submodule is."""
    design = elaborate(
        [str(Path(__file__).parent / "golden_rtl" / "vhdl_flop_min" / "sub.vhd")], top="sub"
    )
    sub = design.definition(design.top)

    by_target = {ca.target: ca for ca in sub.continuous_assigns}
    assert by_target["q"] == ContinuousAssign(target="q", source=None)
