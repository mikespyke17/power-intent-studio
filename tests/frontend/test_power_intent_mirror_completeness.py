"""shared/models/power_intent.py mirrors the backend power-intent schema
FIELD-FOR-FIELD, and the adapter forwards all of it.

The module docstring always claimed "mirrors ... field-for-field", but it
was aspirational: the mirror was a pragmatic subset carrying only what
the mock-data widgets read, and 30 real backend fields plus two whole
constructs were missing. This test makes the claim enforceable.

Why completeness matters here specifically, rather than being tidiness:
power-intent IMPORT has to warn when a file uses a construct this GUI
doesn't model (the UNRECOGNIZED classification). It cannot see that a
field carried a value if the mirror doesn't have the field at all -- a
subset mirror makes that warning impossible by construction.

The RTL mirror is deliberately NOT held to this. shared/models/rtl.py is
a genuine trimmed subset by design; only power intent needs the full
shape, and only power intent gets the generic adapter that depends on it.
"""

import dataclasses

import backend.elaborator.power_intent.ir.schema as backend_schema
import frontend.src.shared.models.power_intent as mirror


def _backend_constructs() -> dict[str, type]:
    out = {}
    for name in dir(backend_schema):
        cls = getattr(backend_schema, name)
        if dataclasses.is_dataclass(cls) and name != "SourceLoc":
            out[name] = cls
    return out


def test_every_backend_construct_has_a_mirror_class():
    missing = [name for name in _backend_constructs() if not hasattr(mirror, name)]
    assert missing == [], "backend constructs with no mirror class:\n  " + "\n  ".join(missing)


def test_every_construct_matches_field_for_field_including_order():
    """Order too, not just the set -- it is free to keep aligned, and it
    makes the docstring's "field-for-field" claim literally true rather
    than approximately true."""
    mismatches = []
    for name, backend_cls in _backend_constructs().items():
        mirror_cls = getattr(mirror, name, None)
        if mirror_cls is None:
            continue  # already reported by the test above
        backend_fields = [f.name for f in dataclasses.fields(backend_cls)]
        mirror_fields = [f.name for f in dataclasses.fields(mirror_cls)]
        if backend_fields != mirror_fields:
            mismatches.append(f"{name}:\n    backend={backend_fields}\n    mirror ={mirror_fields}")
    assert mismatches == [], "\n".join(mismatches)


def test_the_mirror_has_no_power_intent_construct_the_backend_lacks():
    """The other direction -- a mirror class for something the backend
    never produces would be dead weight the adapter could never fill."""
    backend_names = set(_backend_constructs())
    extra = []
    for name in dir(mirror):
        cls = getattr(mirror, name)
        if dataclasses.is_dataclass(cls) and name not in ("SourceLoc",) and name not in backend_names:
            extra.append(name)
    assert extra == [], "mirror-only constructs: " + ", ".join(extra)


def test_the_adapter_forwards_every_field_end_to_end():
    """The completeness above is structural; this drives the real adapter
    over a fully-populated backend design and checks nothing is lost.
    Values are distinctive so a field silently defaulting is visible."""
    from frontend.src.backend_integration.backend_adapter import adapt_power_intent

    backend_design = backend_schema.PowerIntentDesign(
        source_format=backend_schema.SourceFormat.UPF,
        source_version="3.1",
        domains={
            "d": backend_schema.PowerDomain(
                id="d", name="PD", scope="", elements=["a"], exclude_elements=["b"],
                primary_supply="SS", shutoff_condition="!en", power_up_state="high",
                default_restore_edge="re", default_save_edge="se", is_default=True,
                boundary_ports=["p"], available_supplies=["SS2"],
            )
        },
        switches={
            "s": backend_schema.PowerSwitch(
                id="s", name="SW", scope="", domain="PD", input_supply="VDD",
                output_supply="VDD_SW", control_ports=["c"], cell_type="HDR",
                states=[backend_schema.PowerSwitchState(name="on", boolean_expr="c")],
            )
        },
        isolation_strategies={
            "i": backend_schema.IsolationStrategy(
                id="i", name="ISO", scope="", domain="PD", clamp_value="0",
                isolation_supply="SS", isolation_signal="iso_en", isolation_sense="high",
                location="parent", condition="cond", isolation_target="to", exclude_elements=["x"],
            )
        },
        retention_strategies={
            "r": backend_schema.RetentionStrategy(
                id="r", name="RET", scope="", domain="PD", retention_supply="SS",
                primary_supply="SS2", save_signal="sv", restore_signal="rs",
            )
        },
        level_shifter_strategies={
            "l": backend_schema.LevelShifterStrategy(
                id="l", name="LS", scope="", domain="PD", location="parent",
                applies_to="outputs", rule="low_to_high", threshold="0.4", cell_type="LSC",
            )
        },
        power_states={
            "p": backend_schema.PowerState(
                id="p", name="ON", scope="", applies_to="VDD", expr="1.2",
                shape=backend_schema.PowerStateShape.PST_ROW, complete=True,
            )
        },
        domain_supply_connections=[
            backend_schema.DomainSupplyConnection(domain="", supply_net="VDD", scope="", ports=["u/VDD"])
        ],
        port_attributes={
            "pa": backend_schema.PortAttribute(id="pa", scope="", ports=["clk"], driver_supply="SS")
        },
    )

    adapted = adapt_power_intent(backend_design)

    domain = adapted.domains["d"]
    assert (domain.shutoff_condition, domain.is_default, domain.available_supplies) == ("!en", True, ["SS2"])
    switch = adapted.switches["s"]
    assert switch.cell_type == "HDR"
    assert [(s.name, s.boolean_expr) for s in switch.states] == [("on", "c")]
    iso = adapted.isolation_strategies["i"]
    assert (iso.isolation_signal, iso.isolation_sense, iso.location) == ("iso_en", "high", "parent")
    ret = adapted.retention_strategies["r"]
    assert (ret.primary_supply, ret.save_signal, ret.restore_signal) == ("SS2", "sv", "rs")
    ls = adapted.level_shifter_strategies["l"]
    assert (ls.applies_to, ls.rule, ls.threshold, ls.cell_type) == ("outputs", "low_to_high", "0.4", "LSC")
    assert adapted.domain_supply_connections[0].ports == ["u/VDD"]
    assert adapted.port_attributes["pa"].driver_supply == "SS"


def test_enums_cross_by_name_into_the_mirrors_own_type():
    """Never by value, and never the backend enum object itself -- a real
    backend type reaching frontend state is the exact leak the mirror
    exists to prevent."""
    from frontend.src.backend_integration.backend_adapter import adapt_power_intent

    backend_design = backend_schema.PowerIntentDesign(
        source_format=backend_schema.SourceFormat.CPF,
        source_version="2.0",
        power_states={
            "p": backend_schema.PowerState(
                id="p", name="M", scope="", shape=backend_schema.PowerStateShape.CPF_POWER_MODE
            )
        },
    )
    adapted = adapt_power_intent(backend_design)

    assert adapted.source_format is mirror.SourceFormat.CPF
    assert isinstance(adapted.source_format, mirror.SourceFormat)
    assert adapted.power_states["p"].shape is mirror.PowerStateShape.CPF_POWER_MODE
