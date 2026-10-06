"""Hand-built expected IR for the golden_rtl/*_flop_min fixtures.

This is the golden/expected output both test_ir_schema.py (schema-shape
validation) and test_verilog_elaborate.py (real pyslang-driven parser
output) check against. Defined once here so the two don't drift apart.
"""

from backend.elaborator.rtl.ir import (
    BitRange,
    Definition,
    Design,
    Edge,
    Instance,
    Port,
    PortConnection,
    PortDirection,
    ResetKind,
    SequentialElement,
    SourceLanguage,
    make_definition_id,
)


def build_sub(language: SourceLanguage, source_file: str) -> Definition:
    definition_id = make_definition_id("sub", {})
    return Definition(
        definition_id=definition_id,
        base_name="sub",
        language=language,
        source_file=source_file,
        ports=[
            Port("clk", PortDirection.IN),
            Port("rst_n", PortDirection.IN),
            Port("d", PortDirection.IN, BitRange(7, 0)),
            Port("q", PortDirection.OUT, BitRange(7, 0)),
        ],
        sequential_elements=[
            SequentialElement(
                output_signal="q",
                is_latch=False,
                clock_ref="clk",
                edge=Edge.RISING,
                reset_ref="rst_n",
                reset_kind=ResetKind.ASYNC,
                reset_active_low=True,
                data_ref="d",  # `q <= d;` -- a clean bare pass-through in both fixture languages
            )
        ],
    )


def build_sub_no_reset(language: SourceLanguage, source_file: str) -> Definition:
    definition_id = make_definition_id("sub", {})
    return Definition(
        definition_id=definition_id,
        base_name="sub",
        language=language,
        source_file=source_file,
        ports=[
            Port("clk", PortDirection.IN),
            Port("d", PortDirection.IN, BitRange(7, 0)),
            Port("q", PortDirection.OUT, BitRange(7, 0)),
        ],
        sequential_elements=[
            SequentialElement(
                output_signal="q",
                is_latch=False,
                clock_ref="clk",
                edge=Edge.RISING,
                data_ref="d",  # `q <= d;` -- a clean bare pass-through in both fixture languages
            )
        ],
    )


def build_top(language: SourceLanguage, source_file: str, sub_definition_id: str) -> Definition:
    return Definition(
        definition_id=make_definition_id("top", {}),
        base_name="top",
        language=language,
        source_file=source_file,
        ports=[
            Port("clk", PortDirection.IN),
            Port("rst_n", PortDirection.IN),
            Port("data_in", PortDirection.IN, BitRange(7, 0)),
            Port("data_out", PortDirection.OUT, BitRange(7, 0)),
        ],
        instances=[
            Instance(
                name="u_sub",
                definition_id=sub_definition_id,
                connections=[
                    PortConnection("clk", "clk"),
                    PortConnection("rst_n", "rst_n"),
                    PortConnection("d", "data_in"),
                    PortConnection("q", "data_out"),
                ],
            )
        ],
    )


def build_design(language: SourceLanguage, sub_file: str, top_file: str) -> Design:
    sub = build_sub(language, sub_file)
    top = build_top(language, top_file, sub.definition_id)
    return Design(definitions={sub.definition_id: sub, top.definition_id: top}, top=top.definition_id)
