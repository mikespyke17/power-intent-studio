module leaf_a (input logic clk);
  logic q;
  always_ff @(posedge clk) q <= ~q;
endmodule

module leaf_b (input logic clk);
  logic q;
  always_ff @(posedge clk) q <= ~q;
endmodule

// generate if/else if/else -- exactly one branch is ever actually
// elaborated; the other(s) must not contribute anything, and the live
// one's content (an instance, here) must not be silently invisible.
module gen_if_select #(
  parameter int unsigned SIZE = 1
) (
  input logic clk
);
  if (SIZE == 1) begin : g_a
    leaf_a u_leaf (.clk(clk));
  end else begin : g_b
    leaf_b u_leaf (.clk(clk));
  end
endmodule

// generate for -- N instances, one per iteration, each declared with the
// *same* literal instance name inside the loop body.
module gen_for_array #(
  parameter int unsigned N = 3
) (
  input logic clk
);
  genvar i;
  generate
    for (i = 0; i < N; i = i + 1) begin : g_tap
      leaf_a u_inst (.clk(clk));
    end
  endgenerate
endmodule

module top (input logic clk);
  gen_if_select #(.SIZE(1)) u_sel (.clk(clk));
  gen_for_array #(.N(3)) u_arr (.clk(clk));
endmodule
