module clk_gen #(
  parameter int unsigned N = 2
) (
  input  logic         clk_ref,
  input  logic [N-1:0] en,
  output logic [N-1:0] clk_dom
);
  for (genvar d = 0; d < N; d++) begin : g_dom
    assign clk_dom[d] = clk_ref & en[d];
  end
endmodule
