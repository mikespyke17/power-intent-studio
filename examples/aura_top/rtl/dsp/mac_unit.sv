// ---------------------------------------------------------------------------
// mult_add -- registered signed multiply-accumulate primitive.
// ---------------------------------------------------------------------------
module mult_add
  import aura_dsp_pkg::*;
(
  input  logic  clk,
  input  logic  rst_n,
  input  logic  en,
  input  samp_t a,
  input  coef_t b,
  input  acc_t  acc_in,
  output acc_t  acc_out
);

  logic signed [PROD_W-1:0] prod_q;
  acc_t                     acc_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      prod_q <= '0;
      acc_q  <= '0;
    end else if (en) begin
      prod_q <= a * b;
      acc_q  <= acc_in + acc_t'(prod_q);
    end
  end

  assign acc_out = acc_q;

endmodule : mult_add


// ---------------------------------------------------------------------------
// mac_unit -- one lane of the MAC array: local coefficient register, an
// accumulator and saturation on read-out.
// ---------------------------------------------------------------------------
module mac_unit
  import aura_dsp_pkg::*;
#(
  parameter int unsigned LANE = 0
) (
  input  logic  clk,
  input  logic  rst_n,
  input  logic  clear,
  input  logic  en,
  input  samp_t sample,
  input  coef_t coeff,
  input  logic  coeff_we,
  output acc_t  acc,
  output samp_t result
);

  coef_t coeff_q;
  acc_t  acc_q;
  acc_t  acc_next;
  acc_t  mac_out;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)        coeff_q <= default_tap(LANE % N_TAP);
    else if (coeff_we) coeff_q <= coeff;
  end

  mult_add u_ma (
    .clk     (clk),
    .rst_n   (rst_n),
    .en      (en),
    .a       (sample),
    .b       (coeff_q),
    .acc_in  (acc_q),
    .acc_out (mac_out)
  );

  assign acc_next = clear ? acc_t'(0) : mac_out;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)          acc_q <= '0;
    else if (clear | en) acc_q <= acc_next;
  end

  assign acc = acc_q;

  // Saturating round back down to a sample word.
  localparam int unsigned SHIFT = COEF_W - 2;

  logic signed [ACC_W-1:0] scaled;
  assign scaled = acc_q >>> SHIFT;

  always_comb begin
    if (scaled > ACC_W'( (1 <<< (SAMP_W-1)) - 1))
      result = samp_t'((1 <<< (SAMP_W-1)) - 1);
    else if (scaled < ACC_W'(-(1 <<< (SAMP_W-1))))
      result = samp_t'(-(1 <<< (SAMP_W-1)));
    else
      result = samp_t'(scaled);
  end

endmodule : mac_unit
