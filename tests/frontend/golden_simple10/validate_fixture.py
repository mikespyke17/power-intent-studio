"""Load one hand-authored UPF/CPF fixture through the REAL pipeline and
report what the app actually produced.

Used for the iterative hand-authored-fixture validation exercise (see
this directory's own README.md). Deliberately a reporting tool, not
an assertion harness: the point is to SEE what the app did with a file it
did not write, including the colour-derivation chain, which is checked
separately from whether the right objects exist.

    python3 tests/frontend/golden_simple10/validate_fixture.py <fixture> [upf|cpf]

The permanent regression tests built from these fixtures live in
tests/frontend/test_hand_authored_power_intent.py; this script is what
was used to find what those tests assert.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from backend.api import (  # noqa: E402
    elaborate_power_intent,
    elaborate_rtl,
    run_mapping,
)
from frontend.src.backend_integration.backend_adapter import (  # noqa: E402
    adapt_design,
    adapt_mapped_design,
    adapt_power_intent,
)
from frontend.src.power_intent_view.power_domain_overlay import (  # noqa: E402
    DOMAIN_FIXED_TRANSPARENCY,
)
from frontend.src.power_intent_view.power_intent_import import import_power_intent  # noqa: E402

# Everything this harness needs sits beside it, so it resolves the same
# paths test_hand_authored_power_intent.py does -- pinned by that file's
# own test_the_reporting_harness_points_at_the_same_design_as_these_tests,
# after a version of this harness silently kept using a stale design.
HERE = Path(__file__).resolve().parent
DESIGN_DIR = HERE / "design"
GOLDEN_DIR = HERE / "power_intent"
# Regeneration output: beside the fixtures, but GITIGNORED. It is derived
# (regenerate.py rebuilds it from power_intent/ on demand) and nothing
# asserts against it, so committing it would only add noise -- the
# previous `design_examples/simple10/power_intent_generated/` tree was
# removed on explicit request for exactly that reason.
GENERATED_DIR = HERE / "generated"
RTL_FILES = [str(DESIGN_DIR / "sram.v"), str(DESIGN_DIR / "sub.sv")]
LIB_FILES = [str(DESIGN_DIR / "sram.lib")]
TOP = "sram"


def elaborate_design():
    """The elaborated RTL, in both forms the pipeline needs."""
    backend_design = elaborate_rtl(RTL_FILES, LIB_FILES, top=TOP)
    return backend_design, adapt_design(backend_design)


def load_fixture(path: Path, source_format: str):
    backend_design, rtl = elaborate_design()
    power_intent = elaborate_power_intent(str(path), source_format)
    adapted = adapt_power_intent(power_intent)
    mapped = adapt_mapped_design(run_mapping(power_intent, backend_design), adapted, rtl)
    return import_power_intent(adapted, rtl, mapped, str(path)), rtl


def _rgb(color) -> str:
    return color.name() if color is not None else "-"


def report(imported, rtl) -> None:
    print("=" * 78)
    print("DIAGNOSTICS")
    print("=" * 78)
    if not imported.diagnostics:
        print("  (none)")
    for d in imported.diagnostics:
        print(f"  {d.severity.name:<7} | {d.message}")

    print()
    print("=" * 78)
    print("SUPPLIES")
    print("=" * 78)
    for p in sorted(imported.supply.supply_port_placements.values(), key=lambda p: (p.owner_path, p.name)):
        kind = (
            "macro-pg" if p.fixed
            else "virtual" if p.is_virtual_supply
            else "internal" if p.is_internal_supply
            else "port"
        )
        states = ", ".join(f"{s.name}={s.state_type}:{s.voltage}" for s in p.states) or "-"
        print(f"  {p.name:<14} {kind:<9} at {p.owner_path:<22} edge={p.edge:<6} color={_rgb(p.color)}  states=[{states}]")
    print(f"  connectors: {imported.supply.supply_connector_placements}")

    print()
    print("=" * 78)
    print("SUPPLY SETS  (colour source must be a supply of the same name IN owner_paths)")
    print("=" * 78)
    for s in sorted(imported.domain.supply_sets.values(), key=lambda s: s.name):
        source = _colour_source(s, imported)
        print(f"  {s.name:<16} power={s.power!s:<10} ground={s.ground!s:<10} nwell={s.n_well!s:<8} pwell={s.p_well!s:<8}")
        print(f"  {'':<16} owner_paths={s.owner_paths}")
        print(f"  {'':<16} -> colour source: {source}")

    print()
    print("=" * 78)
    print("POWER DOMAINS  (colour must derive from primary supply set's power rail)")
    print("=" * 78)
    sets = imported.domain.supply_sets
    for d in sorted(imported.domain.domains.values(), key=lambda d: d.name):
        primary = sets.get(d.primary_supply_set)
        secondary = [sets[i].name for i in d.secondary_supply_sets if i in sets]
        expected = _expected_domain_colour(d, imported)
        verdict = "OK" if expected and _rgb(d.color) == expected else ("DERIVED-MISMATCH" if expected else "no source")
        print(f"  {d.name:<14} owner_paths={d.owner_paths}")
        print(f"  {'':<14} primary={primary.name if primary else None!s:<16} secondary={secondary}")
        print(f"  {'':<14} colour={_rgb(d.color)}  transparency={d.transparency}"
              f" (expect {DOMAIN_FIXED_TRANSPARENCY})  expected-from-primary={expected or '-'}  [{verdict}]")

    for label, strategies, supply_field in (
        ("ISOLATION", imported.isolation.strategies, "isolation_supply_set_id"),
        ("RETENTION", imported.retention.strategies, "retention_supply_set_id"),
        ("LEVEL SHIFTER", imported.level_shifter.strategies, None),
    ):
        print()
        print("=" * 78)
        print(f"{label}")
        print("=" * 78)
        if not strategies:
            print("  (none)")
        for s in sorted(strategies.values(), key=lambda s: s.name):
            domain = imported.domain.domains.get(s.power_domain_id)
            print(f"  {s.name:<16} domain={domain.name if domain else None!s:<14} owner_path={s.owner_path}")
            if supply_field:
                ss = sets.get(getattr(s, supply_field))
                print(f"  {'':<16} supply set={ss.name if ss else None} -> colour source {_colour_source(ss, imported) if ss else '-'}")
            print(f"  {'':<16} elements={s.elements}")
            for extra in ("clamp_value", "isolation_control", "isolation_sense", "location",
                          "save_logic", "restore_logic", "applies_to", "rule", "threshold", "cell_type"):
                if hasattr(s, extra):
                    print(f"  {'':<16} {extra}={getattr(s, extra)!r}")

    print()
    print("=" * 78)
    print("POWER SWITCHES")
    print("=" * 78)
    if not imported.power_switch.switches:
        print("  (none)")
    for s in imported.power_switch.switches.values():
        domain = imported.domain.domains.get(s.power_domain_id)
        targets = [imported.supply.supply_port_placements[t].name for t in s.target_ids
                   if t in imported.supply.supply_port_placements]
        print(f"  {s.name:<14} domain={domain.name if domain else None!s:<14} targets={targets}")
        print(f"  {'':<14} in={s.input_supply_port!r} out={s.output_supply_port!r} ctrl={s.control_port!r}"
              f" cell={s.cell_type!r} on={s.on_state!r} off={s.off_state!r}")

    print()
    print("=" * 78)
    print("PORT ATTRIBUTES / MACRO ATTACHMENTS")
    print("=" * 78)
    print(f"  port attributes:   {imported.port_attribute.assignments}")
    print(f"  macro attachments: {imported.macro_power_intent.attachments}")

    print()
    print("=" * 78)
    print("POWER STATE TABLE")
    print("=" * 78)
    if not imported.power_state_table.rows:
        print("  (no rows)")
    names = {p.id: p.name for p in imported.supply.supply_port_placements.values()}
    for row in imported.power_state_table.rows.values():
        cells = ", ".join(f"{names.get(k, k)}={v}" for k, v in row.net_states.items())
        print(f"  {row.state_name:<14} domain={row.power_domain_id}  {cells}")

    print()
    print("=" * 78)
    print("COLOUR DISTINCTNESS ACROSS SUPPLY SETS")
    print("=" * 78)
    _report_distinctness(imported)


def _colour_source(supply_set, imported) -> str:
    """Mirrors resolve_supply_set_color_source()'s own match rule: a
    placed supply whose NAME equals this set's power rail AND whose owner
    path is in this set's own owner_paths. Both halves matter -- a set
    naming the right rail at the wrong scope resolves to nothing, and
    that is a real way the colour chain breaks while the entities all
    look correct."""
    if supply_set is None or not supply_set.power:
        return "none (no power rail)"
    for p in imported.supply.supply_port_placements.values():
        if p.owner_path in supply_set.owner_paths and p.name == supply_set.power:
            return f"{p.name} @ {p.owner_path} = {_rgb(p.color)}"
    by_name = [p for p in imported.supply.supply_port_placements.values() if p.name == supply_set.power]
    if by_name:
        paths = sorted({p.owner_path for p in by_name})
        return f"NONE -- '{supply_set.power}' exists at {paths} but not in owner_paths {supply_set.owner_paths}"
    return f"NONE -- no placed supply named '{supply_set.power}' at all"


def _expected_domain_colour(domain, imported):
    supply_set = imported.domain.supply_sets.get(domain.primary_supply_set)
    if supply_set is None or not supply_set.power:
        return None
    for p in imported.supply.supply_port_placements.values():
        if p.owner_path in supply_set.owner_paths and p.name == supply_set.power:
            return _rgb(p.color)
    return None


def _report_distinctness(imported) -> None:
    from frontend.src.power_intent_view.import_palette import are_visually_distinct

    colours = {}
    for s in imported.domain.supply_sets.values():
        for p in imported.supply.supply_port_placements.values():
            if p.owner_path in s.owner_paths and p.name == s.power:
                colours[s.name] = p.color
                break
    names = sorted(colours)
    if len(names) < 2:
        print(f"  only {len(names)} supply set colour(s) resolved -- nothing to compare")
        return
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            ok = are_visually_distinct(colours[first], colours[second])
            print(f"  {first} {_rgb(colours[first])} vs {second} {_rgb(colours[second])}: "
                  f"{'distinct' if ok else 'TOO CLOSE'}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    path = Path(sys.argv[1]).resolve()
    source_format = sys.argv[2] if len(sys.argv) > 2 else ("cpf" if path.suffix == ".cpf" else "upf")
    print(f"FIXTURE: {path.name}   FORMAT: {source_format}")
    imported, rtl = load_fixture(path, source_format)
    report(imported, rtl)


if __name__ == "__main__":
    main()
