// Golden fixture: a `generate for` instantiating one ordinary (non-black-
// box) leaf register per bit, each with an INDEXED OUTPUT connection to a
// wider parent vector (`.q(q_o[i])`), fed from a WHOLE-vector continuous
// assign (`assign d_next = enable ? d_i : q_o;`) rather than a per-bit
// one -- deliberately REAL logic (a ternary), not a pure pass-through, so
// the trace stops right here as a terminal driver rather than continuing
// on through to `d_i`/`q_o` themselves; isolates the "does an indexed
// query match a whole-vector continuous-assign TARGET" fix on its own,
// separate from the already-correct, unrelated "trace through a pass-
// through assign" behavior. See
// test_verilog_generate_indexed_connectivity.py for what this covers.
module leaf_reg (
    input  logic clk,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk) q <= d;
endmodule

module gen_ordinary_top #(
    parameter int WIDTH = 4
) (
    input  logic             clk,
    input  logic             enable,
    input  logic [WIDTH-1:0] d_i,
    output logic [WIDTH-1:0] q_o
);
    logic [WIDTH-1:0] d_next;
    assign d_next = enable ? d_i : q_o;

    for (genvar i = 0; i < WIDTH; i++) begin : g_bit
        leaf_reg u_reg (
            .clk (clk),
            .d   (d_next[i]),
            .q   (q_o[i])
        );
    end
endmodule
