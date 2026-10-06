"""backend_integration/backend_adapter.py -- targeted regression lock for
real/mirror field-forwarding seams: a new field added to a real backend
dataclass (Port.voltage, Port.related_power_pin, InterfacePort.array_size,
Definition.continuous_assigns) does nothing for the GUI on its own -- this
adapter is the ONE place real backend.api data crosses into the frontend's
own mirrored shapes (see this module's own docstring), and a field left
out of the adapter's own field-by-field copy is silently, permanently
dropped for every real design ever loaded through it, even though the
mirrored shape and every downstream consumer already expect it.

Three real, confirmed gaps found this way (found via real `aura` design
use, reported as "the related power pin is in the .lib but doesn't show
in the Port List"): `_adapt_port()` never forwarded `related_power_pin`;
`_adapt_interface_port()` never forwarded `array_size`; `_adapt_definition()`
never forwarded `continuous_assigns` at all (the single field the entire
connectivity-flattening engine's pass-through/real-logic distinction
depends on -- meaning EVERY real design loaded through this adapter had a
completely empty, always-wrong continuous_assigns list). `Port.voltage`
(the original instance of this exact bug class) is kept here unchanged as
the established precedent this file already existed for."""

from backend.elaborator.rtl.ir import ContinuousAssign as BackendContinuousAssign
from backend.elaborator.rtl.ir import Definition as BackendDefinition
from backend.elaborator.rtl.ir import InterfacePort as BackendInterfacePort
from backend.elaborator.rtl.ir import Port as BackendPort
from backend.elaborator.rtl.ir import PortDirection as BackendPortDirection
from backend.elaborator.rtl.ir import SourceLanguage as BackendSourceLanguage
from frontend.src.shared.models import ContinuousAssign as MockContinuousAssign
from frontend.src.backend_integration.backend_adapter import _adapt_definition, _adapt_interface_port, _adapt_port


def test_adapt_port_forwards_a_resolved_pg_voltage():
    port = BackendPort(name="VDD", direction=BackendPortDirection.PG, voltage="0.80V")

    adapted = _adapt_port(port)

    assert adapted.voltage == "0.80V"


def test_adapt_port_leaves_voltage_none_for_an_ordinary_port():
    port = BackendPort(name="clk", direction=BackendPortDirection.IN)

    adapted = _adapt_port(port)

    assert adapted.voltage is None


def test_adapt_port_forwards_related_power_pin():
    port = BackendPort(name="CE", direction=BackendPortDirection.IN, related_power_pin="VDD")

    adapted = _adapt_port(port)

    assert adapted.related_power_pin == "VDD"


def test_adapt_port_leaves_related_power_pin_none_when_absent():
    port = BackendPort(name="CE", direction=BackendPortDirection.IN)

    adapted = _adapt_port(port)

    assert adapted.related_power_pin is None


def test_adapt_interface_port_forwards_array_size():
    port = BackendInterfacePort(name="mst_port", interface_name="axi_lite_if", modport=None, array_size=2)

    adapted = _adapt_interface_port(port)

    assert adapted.array_size == 2


def test_adapt_definition_forwards_continuous_assigns():
    definition = BackendDefinition(
        definition_id="top",
        base_name="top",
        language=BackendSourceLanguage.VERILOG,
        source_file="top.v",
        continuous_assigns=[BackendContinuousAssign(target="y", source="a"), BackendContinuousAssign(target="z", source=None)],
    )

    adapted = _adapt_definition(definition)

    assert adapted.continuous_assigns == [
        MockContinuousAssign(target="y", source="a"),
        MockContinuousAssign(target="z", source=None),
    ]
