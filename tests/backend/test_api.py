"""backend/api/ -- the first-pass validation-harness facade (see its own
module docstring for scope). Two things confirmed explicitly, not just
by manual check during implementation: elaborate_rtl(lib_files=[])
degrades to identical output on a no-black-box design, and a mixed
Verilog+VHDL file set fails with the clear, expected error rather than
something that looks like an unrelated bug.
"""

from pathlib import Path

import pytest

from backend.api import elaborate_power_intent, elaborate_rtl, run_mapping, run_validation
from backend.elaborator.rtl import verilog
from backend.validation import Severity

VERILOG_HIERARCHY_DIR = Path(__file__).parent / "golden_rtl" / "verilog_three_level_hierarchy"
VHDL_FIXTURE_DIR = Path(__file__).parent / "golden_rtl" / "vhdl_flop_min"
UPF_DIR = Path(__file__).parent / "golden_upf" / "upf_basic"
CPF_DIR = Path(__file__).parent / "golden_cpf" / "cpf_basic"


def test_elaborate_rtl_with_no_lib_files_matches_plain_elaborate_exactly():
    """The degradation case: a design with zero black-box instances,
    elaborated via elaborate_rtl(lib_files=[]) (backend/api's wrapper
    around elaborate_with_black_boxes), must produce a Design identical
    to calling verilog.elaborate() directly -- not just "no crash," a
    real equality check on the two Design objects.
    """
    files = [str(VERILOG_HIERARCHY_DIR / "top.v"), str(VERILOG_HIERARCHY_DIR / "mid.v"), str(VERILOG_HIERARCHY_DIR / "leaf.v")]

    via_api = elaborate_rtl(files, lib_files=[], top="top")
    via_direct = verilog.elaborate(files, top="top")

    assert via_api == via_direct


def test_mixed_verilog_vhdl_file_set_raises_the_clear_single_language_error():
    """Confirms the actual failure mode a user hits if they accidentally
    hand the harness a mixed file set: a specific, named ValueError from
    _detect_rtl_language, raised before any elaboration is attempted --
    not a generic parser crash or something that looks like an unrelated
    bug.
    """
    files = [str(VERILOG_HIERARCHY_DIR / "top.v"), str(VHDL_FIXTURE_DIR / "top.vhd")]

    with pytest.raises(ValueError, match="single-language RTL design only"):
        elaborate_rtl(files, lib_files=[], top="top")


def test_elaborate_power_intent_upf():
    power_intent = elaborate_power_intent(str(UPF_DIR / "top.upf"), source_format="upf")
    assert power_intent.source_format.value == "upf"
    assert "::PD_TOP" in power_intent.domains


def test_elaborate_power_intent_cpf():
    power_intent = elaborate_power_intent(str(CPF_DIR / "top.cpf"), source_format="cpf")
    assert power_intent.source_format.value == "cpf"


def test_run_mapping_and_run_validation_against_real_data():
    files = [str(VERILOG_HIERARCHY_DIR / "top.v"), str(VERILOG_HIERARCHY_DIR / "mid.v"), str(VERILOG_HIERARCHY_DIR / "leaf.v")]
    rtl = elaborate_rtl(files, lib_files=[], top="top")
    power_intent = elaborate_power_intent(str(UPF_DIR / "top.upf"), source_format="upf")

    mapped = run_mapping(power_intent, rtl)
    assert len(mapped.references) > 0

    report = run_validation(mapped)
    assert any(f.severity == Severity.ERROR for f in report.findings)  # upf_basic's undeclared VSS, same as test_validation.py
