// Golden fixture: a `generate for` loop directly containing an `always_ff`
// per iteration, with NO submodule instantiation at all -- Category 2 of
// the generate-block validation pass. See
// test_verilog_generate_indexed_connectivity.py for what this covers.
module gen_inline_flops #(
    parameter int WIDTH = 3
) (
    input  logic             clk,
    input  logic             reset_n,
    input  logic [WIDTH-1:0] d_i,
    output logic [WIDTH-1:0] q_o
);
    for (genvar i = 0; i < WIDTH; i++) begin : g_bit
        always_ff @(posedge clk or negedge reset_n) begin
            if (!reset_n) q_o[i] <= 1'b0;
            else          q_o[i] <= d_i[i];
        end
    end
endmodule
