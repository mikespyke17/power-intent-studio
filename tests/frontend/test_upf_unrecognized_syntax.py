"""UPF syntax the app does not recognize is REPORTED, with its line.

Explicit request: when a UPF uses a command or an option this app does
not recognize, mapping must not fail -- the construct is skipped and
flagged in the Diagnostic section, and the message names the line of
the UPF it came from. Everything else in the file is still mapped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.api import elaborate_power_intent
from frontend.src.backend_integration.backend_adapter import adapt_power_intent
from frontend.src.power_intent_view.power_intent_import import (
    UNRECOGNIZED_SYNTAX_CODE,
    import_power_intent,
)
from frontend.src.shared.mock_backend import build_mock_design
from frontend.src.shared.models.validation import Severity

UPF = """\
upf_version 3.1
create_supply_port VDD
create_supply_net VDD
set_design_attributes -elements {.} -attribute foo bar
create_supply_port VSS
create_supply_net VSS
create_supply_set SS -function {power VDD} -function {ground VSS} -bogus_flag 1
create_power_domain PD_TOP \\
    -elements {.} \\
    -supply {primary SS} \\
    -not_a_real_option x
"""


@pytest.fixture
def upf_file(tmp_path: Path) -> Path:
    path = tmp_path / "unrecognized.upf"
    path.write_text(UPF)
    return path


def test_unrecognized_commands_and_options_do_not_stop_elaboration(upf_file):
    design = elaborate_power_intent(str(upf_file), "upf")
    # The recognized commands around them were all read.
    assert {p.name for p in design.supply_ports.values()} == {"VDD", "VSS"}
    # ...and each unrecognized one is recorded with its own line.
    found = [(u.command, u.source_loc.line) for u in design.unrecognized]
    assert found == [("set_design_attributes", 4), ("create_supply_set", 7), ("create_power_domain", 8)]
    assert all(u.source_loc.file == str(upf_file) for u in design.unrecognized)


def test_a_command_with_an_unrecognized_option_is_skipped_whole(upf_file):
    """Half-applying a command whose meaning includes an option the app
    does not understand would be a silent wrong answer -- so the whole
    command is skipped, and reported."""
    design = elaborate_power_intent(str(upf_file), "upf")
    assert design.supply_sets == {}
    assert design.domains == {}
    reasons = {u.command: u.reason for u in design.unrecognized}
    assert "-bogus_flag" in reasons["create_supply_set"]
    assert "-not_a_real_option" in reasons["create_power_domain"]
    assert "not recognized" in reasons["set_design_attributes"]


def test_each_one_is_a_diagnostic_naming_the_upf_line(upf_file):
    design = adapt_power_intent(elaborate_power_intent(str(upf_file), "upf"))
    imported = import_power_intent(design, build_mock_design(), None, str(upf_file))
    flagged = [d for d in imported.diagnostics if d.code == UNRECOGNIZED_SYNTAX_CODE]
    assert [(d.severity, d.line) for d in flagged] == [
        (Severity.WARNING, 4), (Severity.WARNING, 7), (Severity.WARNING, 8),
    ]
    assert all(d.file == str(upf_file) for d in flagged)
    assert "line 4" in flagged[0].message and "set_design_attributes" in flagged[0].message
    assert "line 7" in flagged[1].message and "-bogus_flag" in flagged[1].message
    assert "line 8" in flagged[2].message and "-not_a_real_option" in flagged[2].message


def test_recognized_constructs_carry_their_line_too(upf_file):
    design = elaborate_power_intent(str(upf_file), "upf")
    vss = next(p for p in design.supply_ports.values() if p.name == "VSS")
    assert vss.source_loc.line == 5


# --- CPF is no longer a Load Power Intent format ---------------------------


def test_load_power_intent_offers_upf_files_only():
    from frontend.src.power_intent_view import load_power_intent_tab as tab

    assert tab._POWER_INTENT_FILE_FILTER == "UPF Files (*.upf)"
    assert tab.SOURCE_FORMAT == "upf"


def test_a_cpf_file_is_refused_with_a_diagnostic_before_anything_reads_it(qtbot):
    from frontend.src.power_intent_view.load_power_intent_tab import CPF_NOT_SUPPORTED_CODE, LoadPowerIntentTab

    calls = []
    tab = LoadPowerIntentTab(
        load_action=lambda file, fmt: calls.append((file, fmt)) or (None, []),
        mapping_action=lambda: (None, []),
    )
    qtbot.addWidget(tab)
    tab.files_list.addItem("/designs/top.cpf")
    received = []
    tab.loadDiagnosticsReady.connect(received.append)
    tab._finish_real_pipeline()

    assert calls == []  # the CPF file was never handed to the elaborator
    (diagnostics,) = received
    assert [(d.severity, d.code) for d in diagnostics] == [(Severity.ERROR, CPF_NOT_SUPPORTED_CODE)]
    assert "UPF only" in diagnostics[0].message
