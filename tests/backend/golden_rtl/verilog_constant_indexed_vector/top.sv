module top(
  input logic clk_ref,
  input logic en0,
  input logic en1
);
  localparam int unsigned DOM_A = 0;
  localparam int unsigned DOM_B = 1;

  logic [1:0] clk_dom;

  clk_gen #(.N(2)) u_gen (
    .clk_ref (clk_ref),
    .en      ({en1, en0}),
    .clk_dom (clk_dom)
  );

  consumer u_cons_a (.clk (clk_dom[DOM_A]));
  consumer u_cons_b (.clk (clk_dom[DOM_B]));
endmodule
