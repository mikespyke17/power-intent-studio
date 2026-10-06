"""SequentialElement.data_ref (VHDL) -- see test_sequential_data_ref.py's
own module docstring for the full "why". Reuses three EXISTING VHDL
fixtures already established elsewhere in this suite (no new fixtures
needed): vhdl_flop_min (async reset), vhdl_flop_sync_reset (sync reset
via a real Yosys $mux), vhdl_data_mux_not_reset (a genuine, non-reset
2:1 data mux, already confirmed correctly NOT misidentified as a reset).

UNVERIFIED IN THIS ENVIRONMENT: ghdl/yosys are not installed here (see
tests/backend/test_vhdl_elaborate.py's own skip guard, reused
identically below) -- written and reviewed against the documented Yosys
JSON shape (confirmed via this project's own spike investigation into
_find_reset_mux()/_resolve_bits_to_name()'s existing, already-verified
behavior for clock_ref/reset_ref), but not actually executed against a
real GHDL/yosys run. Run this file in an environment with both installed
(e.g. OSS CAD Suite) to confirm it passes before trusting it fully.
"""

import shutil
from pathlib import Path

import pytest

from backend.elaborator.rtl.vhdl import elaborate

pytestmark = pytest.mark.skipif(
    shutil.which("ghdl") is None or shutil.which("yosys") is None,
    reason="ghdl/yosys not on PATH -- see docs/milestone1_rtl_elaborator.md for setup (OSS CAD Suite or equivalent)",
)

ASYNC_RESET_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_flop_min"
SYNC_RESET_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_flop_sync_reset"
DATA_MUX_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_data_mux_not_reset"


def test_vhdl_async_reset_flop_data_ref_is_d():
    """`vhdl_flop_min`: `q <= d;` outside the reset branch, ARST is a
    separate $adff control pin from D -- D is the real data path
    directly, no mux involved."""
    design = elaborate([str(ASYNC_RESET_DIR / "sub.vhd")], top="sub")
    sub = design.definition(design.top)
    element = sub.sequential_elements[0]
    assert element.data_ref == "d"


def test_vhdl_sync_reset_flop_data_ref_is_d_not_the_mux_output():
    """`vhdl_flop_sync_reset`: GHDL compiles the sync reset into a $mux
    (constant reset value vs. real data `d`) feeding a plain $dff --
    data_ref must resolve through the mux to "d" itself, not the mux's
    own (likely unnamed) Y output."""
    design = elaborate([str(SYNC_RESET_DIR / "sub.vhd")], top="sub")
    sub = design.definition(design.top)
    element = sub.sequential_elements[0]
    assert element.data_ref == "d"


def test_vhdl_genuine_data_mux_yields_no_data_ref():
    """`vhdl_data_mux_not_reset`: `q <= a when sel='1' else b;` -- a
    REAL 2:1 data mux (two real signals, no constant side), already
    confirmed elsewhere in this suite as correctly NOT misidentified as
    a reset (_find_reset_mux requires exactly one constant side). D is
    fed by that mux's own (unnamed) Y output -- data_ref must be None,
    not a guess at either `a` or `b`."""
    design = elaborate([str(DATA_MUX_DIR / "sub.vhd")], top="sub")
    sub = design.definition(design.top)
    element = sub.sequential_elements[0]
    assert element.data_ref is None
