"""Re-export every hand-authored fixture through the app's own writer.

Loads each fixture in this directory's power_intent/, then writes what the
app would generate from the imported state into `generated/` beside them
(gitignored -- it is derived output, rebuilt by running this). Two things
this is good for:

  - a side-by-side of "what a designer wrote" against "what this app
    would write for the same intent", which is where a lossy import shows
    up as a missing command rather than as a diagnostic;
  - exercising generate-AFTER-import, a path neither the round-trip suite
    (which generates from hand-built state) nor validate_fixture.py
    (which stops at the import) covers on its own.

    python3 tests/frontend/golden_simple10/regenerate.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from frontend.src.power_intent_view.power_intent_text_export import (  # noqa: E402
    generate_cpf,
    generate_upf,
)
from validate_fixture import GOLDEN_DIR, GENERATED_DIR, TOP, load_fixture  # noqa: E402

FIXTURES = [
    ("01_designer_domains_first.upf", "upf"),
    ("02_vendor_dense_switch_iso_ret.upf", "upf"),
    ("03_hier/sram_top.upf", "upf"),
    ("04_cpf_modes.cpf", "cpf"),
    ("05_legacy_pst_shared_set.upf", "upf"),
    ("06_wells_and_refinement.upf", "upf"),
    ("07_dangling_refs.upf", "upf"),
    ("08_retention_all_elements.upf", "upf"),
    ("09_supply_port_directions.upf", "upf"),
]


def regenerate(name: str, source_format: str) -> Path:
    imported, rtl = load_fixture((GOLDEN_DIR / name).resolve(), source_format)
    top_ports = rtl.definitions[rtl.top].ports
    common = dict(
        design_name=TOP,
        isolation_state=imported.isolation,
        port_attribute_state=imported.port_attribute,
        top_level_ports=top_ports,
        power_state_table_state=imported.power_state_table,
        macro_power_intent_state=imported.macro_power_intent,
    )
    if source_format == "cpf":
        text = generate_cpf(
            imported.supply, imported.domain, imported.retention, "2.0",
            imported.power_switch, imported.level_shifter, **common,
        )
    else:
        text = generate_upf(
            imported.supply, imported.domain, imported.retention, "3.1",
            imported.power_switch, imported.level_shifter,
            output_dir=str(GENERATED_DIR), **common,
        )
    # A nested fixture (03_hier/sram_top.upf) flattens to one output file.
    out = GENERATED_DIR / (Path(name).name.replace("sram_top", "03_hier_flattened"))
    out.write_text(text)
    return out


def main() -> None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for name, source_format in FIXTURES:
        out = regenerate(name, source_format)
        print(f"{name:<42} -> {out.relative_to(REPO)}  ({len(out.read_text().splitlines())} lines)")


if __name__ == "__main__":
    main()
