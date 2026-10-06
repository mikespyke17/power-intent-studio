# Changelog

All notable changes to Power Intent Studio are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] — 2026-10-06

### Highlights

- **Reworked UPF output.** The generator now writes a flat, top-scope UPF and reproduces a
  hand-fixed reference file exactly (`tests/frontend/test_reference_upf_golden.py`).
- **New reference example, `examples/aura_top`**: a SystemVerilog SoC with six power domains,
  three switches, isolation, retention, level shifters, SRAM macros and a seven-state power
  state table, built from interfaces and generate blocks. The README and the new
  [`docs/example_aura_top.md`](docs/example_aura_top.md) are built around it, with screenshots.
- **Loading a UPF reproduces the project.** Mapping `aura_top`'s own generated UPF back onto
  its RTL + LIB gives back `aura_top.pis`, and UPF syntax the app does not recognise is
  reported with its line instead of stopping the load.

### Changed: UPF generation

- **Flat, root-scoped UPF.** Every construct is declared at the top scope, and a nested power
  domain gives its hierarchy as a root-relative `-elements` path. There are no more
  `set_scope <hierarchy>` blocks and no `create_supply_net … -reuse` copies: each net is
  declared once.
- **Bare references.** Supply nets and sets are named bare everywhere (`add_supply_state`,
  `create_pst -supplies`, `add_power_state -supply`, `-logic_expr`, `-available_supplies`).
- **`create_supply_set`** writes one `-function` per continued line.
- **`set_retention`** names `-retention_supply_set` only (the primary is the domain's own),
  lists root-relative elements, and is followed by a blank line and its
  `set_retention_control`. The save/restore signals are written as `{signal high|low}`
  (`enable` becomes `{enable high}`, `!enable` becomes `{enable low}`).
- **Isolation and level-shifter elements** are root-relative paths.
- **Multi-bit elements** with every bit selected are written `<path>[*]`, for example
  `u_mem/u_ctrl/addr_q[*]`.
- **`-available_supplies { SS }`** is spaced inside the braces.
- **Legacy power state table**: every `add_pst_state` name is prefixed `PST_`, so a mode can
  never share a name with a supply state such as `FULL_ON`.
- **A power switch spanning several hierarchies** is written once, at the top.
- **The header** names the generator version (`Power Intent Studio v1.1`).

### Added

- **`set_isolation -source / -sink`**, written when the Isolation dialog's optional Source /
  Sink Supply Set is set, and read back on import.
- **Isolation "Applies To All: Inputs | Outputs | Both | Elements"**, as Level Shifter already
  has. A direction writes `-applies_to <dir>` with no `-elements`; "Elements" enables the
  element picker. Shown in the Info dialog and a new Properties column, and the badge goes on
  the domain's boundary blocks.
- **Hidden hierarchies are saved with the project.** A hierarchy hidden in the diagram stays
  hidden after Save / Load Project. Projects saved before this load with nothing hidden.
- **Unrecognised UPF syntax is reported, not fatal.** An unknown command, or a known command
  with an unknown option, is skipped whole and listed in the mapping diagnostics as a warning
  quoting the command and naming its UPF line; the rest of the file still maps.
- **Importing the flat UPF form**: the importer recovers a nested internal supply's and supply
  set's hierarchy from the domains, defaults a retention strategy's primary supply to its
  domain's, and reads `{signal high|low}` back.

### Changed: application

- **The app looks the same on every machine.** It no longer takes its style and colours from
  the host desktop (a server in dark mode used to make the whole app dark). It always uses the
  Fusion style with a fixed light palette, on Linux, Windows and macOS.
- **Properties → Power Domain → Exclude Elements** lists one hierarchy per line.

### Removed

- **CPF in Load Power Intent.** The file picker offers `*.upf` only, and a `.cpf` file is
  refused with an error diagnostic before anything reads it. CPF *generation* (Extract Power
  Intent) is unchanged.

### Fixed

- **Mapping a UPF this app generated reproduces the project.** Loading `examples/aura_top`'s
  RTL + LIB and mapping `upf/aura_top.legacy.upf` now gives back `aura_top.pis` exactly, with no
  diagnostics; the modern file matches everything its format can carry (per-supply state
  names, and the states of a supply in no supply set, are not in a modern file). Pinned by
  `tests/frontend/test_upf_mapping_aura_top.py`. What was wrong:
  - `add_port_state` / `add_supply_state` (the legacy form the app writes) were not
    recognised, so a legacy file did not load at all.
  - A rail used by a nested domain was drawn only at the top: the per-hierarchy copies the app
    makes for a shared rail are now rebuilt, wired to the top-level port.
  - Legacy PST rows lost their per-supply state names and kept the generator's `PST_` prefix;
    rows now go to the domain owning the design root.
  - A macro PG pin connects to the nearest same-named supply, an empty switch cell type stays
    empty, and a supply set used only as a secondary gets the hierarchy of the domain using it.
- **A bit-selected isolation signal made the UPF unreadable.** `-isolation_signal iso_en_w[2]`
  was written bare, which Tcl, and therefore every UPF reader, treats as command
  substitution: the file was rejected with `invalid command name "2"`. Values Tcl would
  misread are now braced (`{iso_en_w[2]}`); plain ones are unchanged.
- **SystemVerilog interface signals were missing from the I/O pickers.** The Isolation and
  Level Shifter element lists, the Port Attribute dialog and the isolation badges ignored
  interface ports, so on an interface-based design most of a block's I/O was missing. They now
  list every I/O as Port List does (`bus.rdata`, `ch[0].pwr_en`), with directions, and a
  multi-bit interface signal gets `[*]` in the UPF.
- **`if` / `case` generate blocks were dropped from instance names.** A macro inside
  `if (...) begin : g_1024x32` appeared as `u_wrap/u_macro` instead of
  `u_wrap/g_1024x32.u_macro`, the path UPF and every EDA tool use. Every generate block is now
  a level of the name (an unnamed one by its implicit `genblk<N>`), as `for` generates
  already were. Projects saved before this keep the old names until the design is
  re-elaborated.
- **Legacy UPF wrote the switch-added state as `SWITCH-OFF`**, which is not a valid UPF state
  name. It is now `SWITCH_OFF`; the name shown in the app is unchanged.
- **A level shifter set to "Elements" also got `-applies_to elements`**, which is not valid
  UPF. It now writes `-elements` only.
- **Importing a hierarchical strategy element** (`u_sub/o_q`) dropped the top instance from
  its path.
- **Root-scoped strategies imported onto the top block.** A strategy's badge now goes on its
  domain's block.

---

## [1.0.0-rc1] — 2026-09-26

First release candidate. Everything below is new.

### Design elaboration

- Elaborates **Verilog and SystemVerilog** via slang, including packages, generate blocks,
  interfaces and modports, parameters and arrayed instances.
- Elaborates **VHDL** via GHDL + Yosys, and resolves **mixed-language** hierarchies where a
  VHDL entity instantiates a Verilog module or the reverse.
- Reads **Liberty (`.lib`)** black-box macros for pin, PG-pin and voltage information, with
  `.lib` definitions taking priority over a matching RTL model.
- Extracts **sequential elements** (flip-flops and latches) for retention.
- Traces **connectivity** end to end — through pass-through logic, arbitrary hierarchy depth,
  interfaces and sequential elements.
- Loads an **RTL filelist (`.f`)**: `+define+`, `+incdir+`, nested `-f`/`-F`, comments and
  environment variables, with cycle detection and paths resolved against the filelist.
- Accepts **preprocessor defines** typed directly, combined with any from a filelist.

### Power intent authoring

- **Power domains**, including domains spanning several hierarchies, with colour derived live
  from the primary supply set.
- **Supply ports, internal supplies and virtual supplies**, each with its own power states,
  and supply-set membership across hierarchies.
- **Supply sets** over power, ground, n-well and p-well rails, shareable from an ancestor
  hierarchy.
- **Power switches** with input/output/control/acknowledge ports, control signals and on/off
  state expressions.
- **Isolation** and **level shifter** strategies, with per-element and per-direction control
  and inside/outside placement.
- **Retention** strategies, with element selection across every hierarchy in a domain.
- **Power State Table** with a "Check Power Intent" consistency pass.
- **Port attributes** with per-bit driver/receiver supply assignment.
- Attach an existing **UPF or CPF file to a specific macro instance**.
- Full **undo/redo** across every edit, shared across open diagram tabs.

### Visualization

- Interactive hierarchy diagram with drill-down tabs, zoom, pan, multi-select and hide/unhide.
- A **Design View** and a **Power View** tree, the latter organized by power domain.
- Properties tables for every construct, with glob filtering, sorting and inline edit/remove.
- **Inspect Source File** with syntax highlighting for Verilog/SV/VHDL and Liberty.
- Hover tooltips carrying each element's own fields.

### Import and export

- **Load Power Intent** — read an existing UPF or CPF file, map it onto the elaborated
  hierarchy, and report anything that does not resolve or that the tool does not model.
- **Extract Power Intent** — generate **UPF 3.0 / 3.1** and **CPF 2.0 / 2.1**, with a choice
  of the modern power-state group or the legacy power state table.
- **Extract Diagram** — export open diagram views to PNG, JPEG or SVG.
- **Save / Load Project** — the entire session in a single compressed `.pis` file.

### Quality

- Roughly 2,500 regression tests ship with the project, covering both elaborators, mapping and
  validation, every GUI surface, and round-trip fidelity of the generated UPF and CPF.
- Generated UPF is checked against IEEE 1801 and against hand-authored reference files;
  generated CPF is checked against the Si2 Common Power Format specification.

### Known limitations

- VHDL requires GHDL with the `ghdl-yosys-plugin` installed separately; packaging it is not
  yet resolved.
- Composing mixed-language RTL with `.lib` black-box resolution in one design is not yet
  supported.
- CPF cannot express supply sets, a switch's output supply, a retention supply role, or a
  file-loading construct. Where the format has no equivalent, the tool reports it rather than
  inventing syntax.
- The optional LLM-assisted editing and the diff view are not part of this release.
- **Generated output has not yet been run through a commercial EDA tool.** It is checked
  against the specifications and against hand-authored reference files, and round-tripped
  through this project's own elaborators — which is not the same thing as a vendor tool
  accepting it. Closing that gap is what this release candidate is for.

[1.1.0]: https://github.com/mikespyke17/power-intent-studio/releases/tag/v1.1.0
