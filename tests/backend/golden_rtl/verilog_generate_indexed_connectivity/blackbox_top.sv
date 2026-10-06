// Golden fixture: a `generate for` instantiating one black-box (.lib)
// macro per iteration, each with its OWN locally-declared net + continuous
// assign inside the SAME generate scope (`local_en_b`), plus indexed
// connections to the macro: a genvar-element-select output connection
// (`.q(q_o[i])`) and a literal-bounds range-select input connection
// (`.a(addr_i[7:0])`). See test_verilog_generate_indexed_connectivity.py
// for what this covers -- pairs with tests/backend/golden_lib/gen_macro.lib.
module blackbox_gen_top #(
    parameter int N = 3
) (
    input  logic           clk,
    input  logic           en_b,
    input  logic [1:0]     sel,
    input  logic [7:0]     addr_i,
    output logic [N-1:0]   q_o
);
    for (genvar i = 0; i < N; i++) begin : g_inst
        logic local_en_b;

        // Locally-declared, genuinely distinct per iteration -- real SV:
        // g_inst[0].local_en_b and g_inst[1].local_en_b are different nets.
        assign local_en_b = en_b | (sel != i[1:0]);

        GEN_MACRO u_macro (
            .clk  (clk),
            .en_b (local_en_b),
            .a    (addr_i[7:0]),
            .q    (q_o[i])
        );
    end
endmodule
