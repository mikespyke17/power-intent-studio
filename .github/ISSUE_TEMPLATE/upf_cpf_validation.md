---
name: UPF/CPF validation feedback
about: A real EDA tool rejected or warned about generated UPF/CPF
title: '[vendor] '
labels: vendor-validation
assignees: ''
---

<!--
For findings from running generated UPF/CPF through real EDA tooling.

This is deliberately separate from the generic bug report: a vendor
finding is about the GENERATED FILE's conformance, and what makes it
actionable is the tool's own verbatim message plus the snippet that
provoked it -- not a description of what the GUI did.

Tracked under the `vendor-validation` label. That label is not
git-versioned and must exist in the repository before GitHub will
apply it -- see .github/REPO_SETUP.md.

Reading a specification cannot fully guarantee what a specific tool
accepts. That gap is exactly what this template exists to close, so a
report here is useful even when the construct looks correct per the LRM.
-->

## EDA tool and version

<!-- Be specific -- acceptance differs between versions of the same tool.
     e.g. Synopsys VC LP 2025.06, Cadence Conformal Low Power 23.10,
     Siemens Questa PA 2024.2 -->

- **Tool**:
- **Version**:

## Target format and version

<!-- Which of the app's generation targets produced the file. -->

- [ ] UPF 3.0
- [ ] UPF 3.1
- [ ] CPF 2.0
- [ ] CPF 2.1

Generated via: <!-- Extract > Extract Power Intent > ... -->

## The tool's exact message

<!-- VERBATIM, including the error/warning code and any line reference.
     Paraphrasing loses the detail that identifies which rule was hit. -->

```text

```

Severity as the tool reported it: <!-- error / warning / info -->

## The generated snippet that triggered it

<!-- The smallest piece of the generated file that still provokes the
     message, with a couple of lines either side for context. Please
     include any `set_scope` / `set_design` block it sits inside -- scope
     is load-bearing and a snippet lifted out of one can read as valid
     when it is not. -->

```tcl

```

## Which app feature produced it

<!-- If you know which dialog or tool authored this construct, it points
     straight at the generator code responsible. Tick any that apply. -->

- [ ] Power Domain
- [ ] Supply Port / Internal Supply / Virtual Supply
- [ ] Supply Set
- [ ] Power Switch
- [ ] Isolation
- [ ] Retention
- [ ] Level Shifter
- [ ] Power State Table / power states
- [ ] Port Attributes
- [ ] Macro power intent attachment
- [ ] Not sure

## Repro material, if shareable

<!-- Any ONE of these is usually enough, in decreasing order of
     usefulness. Nothing here is required -- a verbatim message plus a
     snippet is already a useful report. -->

- [ ] The `.pis` project file (`File > Save Project`)
- [ ] The full generated `.upf`/`.cpf`
- [ ] A minimal RTL design that reproduces it
- [ ] None of the above can be shared

<!-- If the design is confidential, a hand-reduced equivalent that still
     provokes the same message is just as good -- and often better. -->

## What the correct output should be, if you know

<!-- Optional, and valuable: the syntax the tool DOES accept, ideally
     with the specification section that backs it. That turns a report
     into a fix. -->

## Environment

- **Power Intent Studio version**: <!-- e.g. 1.1.0, or the commit -->
- **OS**:
- **Python version**:

## Additional context
