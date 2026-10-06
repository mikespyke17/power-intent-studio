module multi_reg (
  input  logic       clk,
  input  logic       rst_n,
  input  logic [1:0] sel,
  output logic [7:0] a_q,
  output logic [7:0] b_q,
  output logic [7:0] c_q
);
  // One always_ff resetting three unrelated registers together, then a
  // case statement driving one of them per branch -- the idiomatic real
  // RTL shape that used to make the whole block contribute nothing.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      a_q <= 8'h00;
      b_q <= 8'h00;
      c_q <= 8'h00;
    end else begin
      case (sel)
        2'b00:   a_q <= a_q + 8'd1;
        2'b01:   b_q <= b_q + 8'd1;
        default: c_q <= c_q + 8'd1;
      endcase
    end
  end
endmodule

module multi_reg_resetless (
  input  logic       clk,
  output logic [7:0] x_q,
  output logic [7:0] y_q
);
  // No if/else at all -- a resetless block with more than one assignment.
  always_ff @(posedge clk) begin
    x_q <= x_q + 8'd1;
    y_q <= y_q + 8'd2;
  end
endmodule
