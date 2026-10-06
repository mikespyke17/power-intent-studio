"""`.pis` project file (explicit request) -- gzip-compressed JSON with
explicit ID-based object sharing. See project_file.py's own module
docstring for the format rationale (corruption-prevention, not
encryption -- user-confirmed)."""

import gzip

import pytest
from PySide6.QtGui import QColor

from frontend.src.app.project_file import ProjectData, ProjectFileError, deserialize_project, serialize_project
from frontend.src.power_intent_view.isolation_overlay import IsolationDiagramState, IsolationStrategyPlacement
from frontend.src.power_intent_view.level_shifter_overlay import LevelShifterDiagramState, LevelShifterStrategyPlacement
from frontend.src.power_intent_view.port_attribute_overlay import PortAttributeDiagramState
from frontend.src.power_intent_view.power_domain_overlay import PowerDomainDiagramState, PowerDomainPlacement, SupplySetPlacement
from frontend.src.power_intent_view.power_state_table import PowerStateTableDiagramState, PowerStateTableRow
from frontend.src.power_intent_view.power_switch_overlay import PowerSwitchDiagramState, PowerSwitchPlacement
from frontend.src.power_intent_view.retention_overlay import RetentionDiagramState, RetentionStrategyPlacement
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortDiagramState, SupplyPortPlacement, SupplyPortState
from frontend.src.shared.mock_backend import build_mock_design


def _rich_project() -> ProjectData:
    design = build_mock_design()
    supply = SupplyPortDiagramState(
        supply_port_placements={
            "vdd": SupplyPortPlacement(
                id="vdd", owner_path="top", name="VDD", color=QColor("#ff8800"), edge="top", t=0.3,
                states=[SupplyPortState("FULL_ON", "1.2"), SupplyPortState("OFF")],
            ),
            "vv": SupplyPortPlacement(id="vv", owner_path="top/u_core", name="VVIRT", color=QColor("#00ccff"), is_virtual_supply=True,
                                      states=[SupplyPortState("FULL_ON", "0.9")]),
        },
        supply_connector_placements=[("vdd", "vv")],
        manual_connector_offsets={("vdd", "vv"): (1, 4.5)},
    )
    shared_set = SupplySetPlacement(id="ss_shared", name="SS_SHARED", power="VDD", ground="VSS", owner_path="top")
    domains = PowerDomainDiagramState(
        supply_sets={"ss_shared": shared_set},
        domains={
            "d1": PowerDomainPlacement(id="d1", owner_path="top/u_core", name="PD_CORE", color=QColor("#22aa22"), transparency=0.4, primary_supply_set="ss_shared"),
            "d2": PowerDomainPlacement(id="d2", owner_path="top/u_dbg", name="PD_DBG", color=QColor("#2222aa"), secondary_supply_sets=["ss_shared"]),
        },
    )
    retention = RetentionDiagramState(strategies={
        "r1": RetentionStrategyPlacement(id="r1", name="RET1", power_domain_id="d1", primary_supply_set_id="ss_shared",
                                         retention_supply_set_id="ss_shared", save_logic="s", restore_logic="r", elements=["top/u_core/q"]),
    })
    switches = PowerSwitchDiagramState(switches={
        "sw1": PowerSwitchPlacement(id="sw1", name="SW1", power_domain_id="d1", target_ids=["vv"], owner_paths=["top/u_core"],
                                    input_supply_port="IN", output_supply_port="OUT", control_port="CTRL", cell_type="HDR", on_state="on", off_state=""),
    })
    isolation = IsolationDiagramState(strategies={
        "i1": IsolationStrategyPlacement(id="i1", name="ISO1", power_domain_id="d1", isolation_supply_set_id="ss_shared",
                                         clamp_value="low", isolation_control="iso_en", isolation_sense="high", elements=["top/u_core/o"]),
    })
    level_shifter = LevelShifterDiagramState(strategies={
        "l1": LevelShifterStrategyPlacement(id="l1", name="LS1", power_domain_id="d1", applies_to="outputs", rule="low_to_high", cell_type="LSCELL", elements=[]),
    })
    port_attr = PortAttributeDiagramState(assignments={"clk": "ss_shared", "rst_n": None})
    pst = PowerStateTableDiagramState(rows={
        "row1": PowerStateTableRow(id="row1", power_domain_id="d1", state_name="ACTIVE", net_states={"vdd": "ON", "vv": "ACTIVE"}),
    })
    return ProjectData("Round Trip Project", design, supply, domains, retention, port_attr, switches, isolation, level_shifter, pst)


# --- (a) full round-trip fidelity ------------------------------------------


def test_a_full_round_trip_reconstructs_identical_state():
    original = _rich_project()

    blob = serialize_project(original)
    restored = deserialize_project(blob)

    assert restored.project_name == "Round Trip Project"
    assert restored.rtl_design.top == original.rtl_design.top
    assert set(restored.rtl_design.definitions) == set(original.rtl_design.definitions)

    vdd = restored.supply_state.supply_port_placements["vdd"]
    assert vdd.name == "VDD" and vdd.edge == "top" and vdd.t == pytest.approx(0.3)
    assert vdd.color.name(QColor.NameFormat.HexArgb) == QColor("#ff8800").name(QColor.NameFormat.HexArgb)
    assert [(s.state_type, s.voltage) for s in vdd.states] == [("FULL_ON", "1.2"), ("OFF", None)]
    assert restored.supply_state.supply_port_placements["vv"].is_virtual_supply is True
    assert restored.supply_state.supply_connector_placements == [("vdd", "vv")]
    assert restored.supply_state.manual_connector_offsets == {("vdd", "vv"): (1, 4.5)}

    assert restored.domain_state.domains["d1"].transparency == pytest.approx(0.4)
    assert restored.domain_state.domains["d1"].color.name() == "#22aa22"
    assert restored.retention_state.strategies["r1"].save_logic == "s"
    assert restored.power_switch_state.switches["sw1"].cell_type == "HDR"
    assert restored.isolation_state.strategies["i1"].clamp_value == "low"
    assert restored.level_shifter_state.strategies["l1"].cell_type == "LSCELL"
    assert restored.port_attribute_state.assignments == {"clk": "ss_shared", "rst_n": None}
    assert restored.power_state_table_state.rows["row1"].net_states == {"vdd": "ON", "vv": "ACTIVE"}


# --- (b) shared Supply Set survives as a single object --------------------


def test_b_supply_set_shared_across_two_domains_stays_a_single_object():
    original = _rich_project()

    restored = deserialize_project(serialize_project(original))

    # One flat supply_sets dict, one object.
    assert list(restored.domain_state.supply_sets) == ["ss_shared"]
    # Both domains reference it by the SAME id.
    assert restored.domain_state.domains["d1"].primary_supply_set == "ss_shared"
    assert restored.domain_state.domains["d2"].secondary_supply_sets == ["ss_shared"]
    # Editing that one object is visible from both domains' resolution --
    # i.e. it was NOT naively duplicated/un-shared on reload.
    the_set = restored.domain_state.supply_sets["ss_shared"]
    the_set.power = "VDD_EDITED"
    assert restored.domain_state.supply_sets[restored.domain_state.domains["d1"].primary_supply_set].power == "VDD_EDITED"
    assert restored.domain_state.supply_sets[restored.domain_state.domains["d2"].secondary_supply_sets[0]].power == "VDD_EDITED"


# --- (c) the on-disk bytes are gzip binary, not JSON text ----------------


def test_c_serialized_bytes_are_gzip_binary_not_plaintext_json():
    blob = serialize_project(_rich_project())

    assert blob[:2] == b"\x1f\x8b"  # gzip magic
    # Not decodable as text/JSON as-is (a plain editor would show garbage).
    with pytest.raises(UnicodeDecodeError):
        blob.decode("utf-8")
    # ...but IS valid gzip'd JSON once decompressed.
    import json

    payload = json.loads(gzip.decompress(blob).decode("utf-8"))
    assert payload["format"] == "power-intent-studio-project"


# --- (d) corrupted / truncated file fails cleanly -----------------------


def test_d_garbage_bytes_raise_projectfileerror_not_a_crash():
    with pytest.raises(ProjectFileError):
        deserialize_project(b"this is definitely not a .pis file")


def test_d_truncated_file_raises_projectfileerror():
    blob = serialize_project(_rich_project())
    with pytest.raises(ProjectFileError):
        deserialize_project(blob[: len(blob) // 2])


def test_d_valid_gzip_but_not_a_project_payload_raises_projectfileerror():
    not_a_project = gzip.compress(b'{"hello": "world"}')
    with pytest.raises(ProjectFileError):
        deserialize_project(not_a_project)


def test_d_wrong_version_raises_projectfileerror():
    import json

    payload = json.loads(gzip.decompress(serialize_project(_rich_project())).decode("utf-8"))
    payload["version"] = 999
    with pytest.raises(ProjectFileError, match="version"):
        deserialize_project(gzip.compress(json.dumps(payload).encode("utf-8")))


# --- Audit fix (category 11): required VALUES, not just required keys -----


def test_a_null_section_is_rejected_as_a_project_file_error_not_a_crash(monkeypatch):
    """A payload whose rtl_design is null passed every gate (format tag,
    version, all required keys present) and then failed deep inside
    load_project() with AttributeError -- which is neither OSError nor
    ProjectFileError, so it escaped the UI's own handler entirely and
    surfaced as an uncaught crash instead of this module's promised
    single clean "could not load" error."""
    import gzip
    import json

    payload = json.loads(gzip.decompress(serialize_project(_rich_project())))
    payload["rtl_design"] = None

    with pytest.raises(ProjectFileError) as excinfo:
        deserialize_project(gzip.compress(json.dumps(payload).encode("utf-8")))
    assert "rtl_design" in str(excinfo.value)


def test_every_required_section_is_value_checked_not_just_rtl_design():
    """None of the required sections are legitimately null, and a null
    in any of them would fail just as opaquely downstream -- so the
    check covers all of them, not only the one the audit reproduced."""
    import gzip
    import json

    base = json.loads(gzip.decompress(serialize_project(_rich_project())))
    for key in ("supply_state", "domain_state", "retention_state", "power_switch_state", "isolation_state"):
        payload = dict(base)
        payload[key] = None
        with pytest.raises(ProjectFileError):
            deserialize_project(gzip.compress(json.dumps(payload).encode("utf-8")))
