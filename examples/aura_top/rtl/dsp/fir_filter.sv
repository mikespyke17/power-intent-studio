// ---------------------------------------------------------------------------
// fir_filter -- transposed-form FIR.  Coefficients default to the window
// generated at elaboration time by aura_dsp_pkg::default_tap and can be
// overwritten one tap at a time from the configuration port.
// ---------------------------------------------------------------------------
module fir_filter
  import aura_dsp_pkg::*;
(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic                  clear,

  input  logic                  in_valid,
  input  samp_t                 in_sample,
  output logic                  out_valid,
  output samp_t                 out_sample,

  input  logic                  tap_we,
  input  logic [TAP_SEL_W-1:0]  tap_addr,
  input  coef_t                 tap_data
);

  coef_t coeff_q [N_TAP];
  acc_t  chain_q [N_TAP];

  // ---- coefficient store ---------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int unsigned t = 0; t < N_TAP; t++) coeff_q[t] <= default_tap(t);
    end else if (tap_we) begin
      coeff_q[tap_addr] <= tap_data;
    end
  end

  // ---- transposed delay chain ---------------------------------------------
  for (genvar t = 0; t < N_TAP; t++) begin : g_tap
    logic signed [PROD_W-1:0] prod;
    assign prod = in_sample * coeff_q[t];

    always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
        chain_q[t] <= '0;
      end else if (clear) begin
        chain_q[t] <= '0;
      end else if (in_valid) begin
        if (t == N_TAP - 1) chain_q[t] <= acc_t'(prod);
        else                chain_q[t] <= chain_q[t+1] + acc_t'(prod);
      end
    end
  end

  // ---- output --------------------------------------------------------------
  localparam int unsigned SHIFT = COEF_W - 2;

  logic                    valid_q;
  logic signed [ACC_W-1:0] scaled;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)      valid_q <= 1'b0;
    else if (clear)  valid_q <= 1'b0;
    else             valid_q <= in_valid;
  end

  assign scaled = chain_q[0] >>> SHIFT;

  always_comb begin
    if (scaled > ACC_W'((1 <<< (SAMP_W-1)) - 1))
      out_sample = samp_t'((1 <<< (SAMP_W-1)) - 1);
    else if (scaled < ACC_W'(-(1 <<< (SAMP_W-1))))
      out_sample = samp_t'(-(1 <<< (SAMP_W-1)));
    else
      out_sample = samp_t'(scaled);
  end

  assign out_valid = valid_q;

endmodule : fir_filter
