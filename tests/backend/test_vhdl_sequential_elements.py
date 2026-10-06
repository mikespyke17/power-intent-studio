"""1h review pass: VHDL sync-reset flop correctness and basic latch support.

Two findings from an explicit audit of 1h against its original scope
("identify sequential elements -- flip-flops and latches -- from
always/process blocks"), not assumed complete just because the flop/latch
mechanism already existed:

1. A real silent-wrong-answer bug, not a coverage gap: GHDL never emits a
   single $sdff-style primitive for a synchronous-reset register -- it
   compiles the reset into a $mux (selecting between the reset value and
   real data) feeding an otherwise-ordinary $dff. Before this fix, such a
   register was reported as reset_kind=NONE (silently resetless), not left
   unrecognized. Fixed in _find_reset_mux / _build_dff.
2. VHDL latches weren't just unimplemented -- by default GHDL/Yosys treats
   latch inference as a hard elaboration error and aborts the whole
   elaborate() call. Fixed by always passing --latches, plus basic
   (enable-only) $dlatch extraction. A latch *with* a reset/set compiles to
   a more complex $not/$mux/$or chain feeding a plain $dlatch -- genuinely
   more complex than the one-hop tracing used elsewhere, and deliberately
   NOT attempted here; confirmed it no longer crashes elaborate() even
   though that specific shape is still left unrecognized.
"""

import shutil
from pathlib import Path

import pytest

from backend.elaborator.rtl.ir import Edge, ResetKind
from backend.elaborator.rtl.vhdl import elaborate

pytestmark = pytest.mark.skipif(
    shutil.which("ghdl") is None or shutil.which("yosys") is None,
    reason="ghdl/yosys not on PATH -- see docs/milestone1_rtl_elaborator.md for setup (OSS CAD Suite or equivalent)",
)

SYNC_RESET_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_flop_sync_reset"
SYNC_RESET_ACTIVE_HIGH_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_flop_sync_reset_active_high"
DATA_MUX_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_data_mux_not_reset"
LATCH_MIN_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_latch_min"
LATCH_WITH_RESET_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_latch_with_reset_no_crash"


def test_sync_reset_active_low_is_correctly_classified_not_reported_as_resetless():
    """The actual bug: before the fix, this exact fixture came back with
    reset_kind=NONE. Asserts the corrected values directly, not just
    "reset_kind != NONE", so a regression back to the old (wrong, not just
    incomplete) behavior fails clearly.
    """
    design = elaborate([str(SYNC_RESET_DIR / "sub.vhd")], top="sub")
    seq = design.definition(design.top).sequential_elements[0]
    assert seq.clock_ref == "clk"
    assert seq.edge == Edge.RISING
    assert seq.reset_ref == "rst_n"
    assert seq.reset_kind == ResetKind.SYNC
    assert seq.reset_active_low is True


def test_sync_reset_active_high_polarity_is_correct_the_other_direction():
    """Same shape, opposite polarity (no inverter between the named signal
    and the mux select) -- checks the polarity math isn't only correct for
    the one case it was derived from.
    """
    design = elaborate([str(SYNC_RESET_ACTIVE_HIGH_DIR / "sub.vhd")], top="sub")
    seq = design.definition(design.top).sequential_elements[0]
    assert seq.reset_ref == "rst"
    assert seq.reset_kind == ResetKind.SYNC
    assert seq.reset_active_low is False


def test_genuine_data_mux_is_not_misidentified_as_a_reset():
    """A plain 2:1 data mux feeding a $dff (two real signal inputs, no
    constant) is a very common, completely unrelated RTL pattern. Must not
    be mistaken for a sync reset -- this is exactly the false-positive risk
    _find_reset_mux's "exactly one constant input" check exists to avoid.
    """
    design = elaborate([str(DATA_MUX_DIR / "sub.vhd")], top="sub")
    seq = design.definition(design.top).sequential_elements[0]
    assert seq.reset_ref is None
    assert seq.reset_kind == ResetKind.NONE


def test_plain_latch_no_longer_crashes_and_is_correctly_identified():
    design = elaborate([str(LATCH_MIN_DIR / "sub.vhd")], top="sub")
    seq = design.definition(design.top).sequential_elements[0]
    assert seq.is_latch is True
    assert seq.output_signal == "q"
    assert seq.clock_ref == "en"  # doubles as the enable/gate signal per the IR schema
    assert seq.edge is None
    assert seq.reset_kind == ResetKind.NONE


def test_latch_with_reset_no_longer_crashes_elaboration_even_though_unrecognized():
    """Before --latches, this design's elaborate() call raised entirely --
    no ports, no hierarchy, nothing usable. Now the design elaborates
    successfully; only this specific (more complex) sequential shape is
    left unrecognized, matching "don't guess" rather than "don't work at all".
    """
    design = elaborate([str(LATCH_WITH_RESET_DIR / "sub.vhd")], top="sub")
    sub = design.definition(design.top)
    assert [(p.name, p.width) for p in sub.ports] == [("en", 1), ("rst_n", 1), ("d", 8), ("q", 8)]
    assert sub.sequential_elements == []
