// ---------------------------------------------------------------------------
// aura_dsp_pkg -- sizing and coefficients for the DSP accelerator
// ---------------------------------------------------------------------------
package aura_dsp_pkg;

  parameter int unsigned N_MAC   = 8;              // parallel MAC lanes
  parameter int unsigned SAMP_W  = 16;             // sample width
  parameter int unsigned COEF_W  = 16;             // coefficient width
  parameter int unsigned PROD_W  = SAMP_W + COEF_W;
  parameter int unsigned ACC_W   = PROD_W + 8;     // 8 bits of headroom
  parameter int unsigned N_TAP   = 16;             // FIR taps
  parameter int unsigned TAP_SEL_W = $clog2(N_TAP);
  parameter int unsigned LANE_SEL_W = $clog2(N_MAC);

  parameter int unsigned DSP_MEM_DEPTH = 512;
  parameter int unsigned DSP_MEM_AW    = $clog2(DSP_MEM_DEPTH);

  typedef enum logic [1:0] {
      DSP_IDLE = 2'b00,
      DSP_LOAD = 2'b01,
      DSP_RUN  = 2'b10,
      DSP_DONE = 2'b11
  } dsp_state_e;

  typedef logic signed [SAMP_W-1:0] samp_t;
  typedef logic signed [COEF_W-1:0] coef_t;
  typedef logic signed [ACC_W-1:0]  acc_t;

  // Default window: a fixed-point raised-cosine-ish taper, generated at
  // elaboration time so the coefficient ROM needs no external file.
  function automatic coef_t default_tap(input int unsigned k);
    int unsigned centre;
    int          dist_v;
    int          mag;
    centre = N_TAP / 2;
    dist_v = (int'(k) >= int'(centre)) ? (int'(k) - int'(centre))
                                       : (int'(centre) - int'(k));
    mag    = (1 << (COEF_W - 2)) - (dist_v * (1 << (COEF_W - 2))) / int'(centre + 1);
    return coef_t'(mag);
  endfunction

endpackage : aura_dsp_pkg
