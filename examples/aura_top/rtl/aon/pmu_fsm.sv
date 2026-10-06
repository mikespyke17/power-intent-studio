// ---------------------------------------------------------------------------
// pmu_fsm -- always-on power sequencer.
//
// Walks the domains one at a time and drives the canonical low-power handshake:
//
//   power down :  clock off -> retention save -> isolation on  -> switch off
//   power up   :  switch on -> retention restore -> isolation off -> reset
//                 release -> clock on
//
// The order matters: isolation must already clamp before the header switch
// opens, and retention restore must happen while the domain is still isolated
// and held in reset.  The UPF isolation / retention strategies are written
// against exactly these signals.
// ---------------------------------------------------------------------------
module pmu_fsm
  import aura_pkg::*;
  import aura_pmu_pkg::*;
#(
  parameter int unsigned N_DOM = aura_pkg::N_DOMAIN
) (
  input  logic         clk_aon,
  input  logic         rst_aon_n,

  input  power_state_e req_state,
  input  logic         req_valid,

  output power_state_e cur_state,
  output logic         busy,
  output logic [N_DOM-1:0] dom_on,

  pmu_if.pmu           ch [N_DOM]
);

  typedef enum logic [3:0] {
      S_IDLE       = 4'd0,
      S_SCAN       = 4'd1,
      S_DN_CLK     = 4'd2,
      S_DN_SAVE    = 4'd3,
      S_DN_ISO     = 4'd4,
      S_DN_PWR     = 4'd5,
      S_UP_PWR     = 4'd6,
      S_UP_RESTORE = 4'd7,
      S_UP_ISO     = 4'd8,
      S_UP_RST     = 4'd9,
      S_UP_CLK     = 4'd10,
      S_NEXT       = 4'd11
  } seq_e;

  localparam logic [N_DOM-1:0] RESET_MASK = STATE_MASK[int'(PS_SHUTDOWN)];

  seq_e                  seq_q;
  logic [3:0]            dom_q;
  logic [CNT_W-1:0]      cnt_q;
  power_state_e          cur_q;
  power_state_e          tgt_q;

  logic [N_DOM-1:0] on_q;
  logic [N_DOM-1:0] pwr_en_q;
  logic [N_DOM-1:0] iso_en_q;
  logic [N_DOM-1:0] ret_save_q;
  logic [N_DOM-1:0] ret_restore_q;
  logic [N_DOM-1:0] clk_en_q;
  logic [N_DOM-1:0] rst_n_q;
  logic [N_DOM-1:0] ack_w;

  // ---- Interface array binding --------------------------------------------
  for (genvar g = 0; g < N_DOM; g++) begin : g_bind
    assign ch[g].pwr_en      = pwr_en_q[g];
    assign ch[g].iso_en      = iso_en_q[g];
    assign ch[g].ret_save    = ret_save_q[g];
    assign ch[g].ret_restore = ret_restore_q[g];
    assign ch[g].clk_en      = clk_en_q[g];
    assign ch[g].rst_n       = rst_n_q[g];
    assign ack_w[g]          = ch[g].pwr_ack;
  end

  // ---- Per-domain attributes for the domain currently being sequenced ------
  logic       want_bit;
  logic       dom_retentive;
  logic       dom_switched;
  logic [2:0] dom_idx;

  assign dom_idx = dom_q[2:0];

  always_comb begin
    want_bit      = 1'b0;
    dom_retentive = 1'b0;
    dom_switched  = 1'b0;
    if (dom_q < N_DOM) begin
      want_bit      = STATE_MASK[int'(tgt_q)][dom_idx];
      dom_retentive = DOM_RETENTIVE[dom_idx];
      dom_switched  = DOM_SWITCHED[dom_idx];
    end
  end

  assign cur_state = cur_q;
  assign busy      = (seq_q != S_IDLE);
  assign dom_on    = on_q;

  // ---- Sequencer -----------------------------------------------------------
  always_ff @(posedge clk_aon or negedge rst_aon_n) begin
    if (!rst_aon_n) begin
      seq_q         <= S_IDLE;
      dom_q         <= '0;
      cnt_q         <= '0;
      cur_q         <= PS_SHUTDOWN;
      tgt_q         <= PS_SHUTDOWN;
      on_q          <= RESET_MASK;
      pwr_en_q      <= RESET_MASK;
      iso_en_q      <= ~RESET_MASK;
      ret_save_q    <= '0;
      ret_restore_q <= '0;
      clk_en_q      <= RESET_MASK;
      rst_n_q       <= RESET_MASK;
    end else begin
      unique case (seq_q)

        S_IDLE: begin
          if (req_valid && (req_state != cur_q)) begin
            tgt_q <= req_state;
            dom_q <= '0;
            cnt_q <= '0;
            seq_q <= S_SCAN;
          end
        end

        S_SCAN: begin
          cnt_q <= '0;
          if (dom_q >= N_DOM) begin
            cur_q <= tgt_q;
            seq_q <= S_IDLE;
          end else if (want_bit == on_q[dom_idx]) begin
            dom_q <= dom_q + 4'd1;
          end else if (!want_bit) begin
            seq_q <= S_DN_CLK;
          end else begin
            seq_q <= S_UP_PWR;
          end
        end

        // ---- power down -----------------------------------------------
        S_DN_CLK: begin
          clk_en_q[dom_idx] <= 1'b0;
          rst_n_q[dom_idx]  <= 1'b0;
          cnt_q             <= '0;
          seq_q             <= dom_retentive ? S_DN_SAVE : S_DN_ISO;
        end

        S_DN_SAVE: begin
          ret_save_q[dom_idx] <= 1'b1;
          if (cnt_q == CNT_W'(T_SAVE - 1)) begin
            ret_save_q[dom_idx] <= 1'b0;
            cnt_q               <= '0;
            seq_q               <= S_DN_ISO;
          end else begin
            cnt_q <= cnt_q + CNT_W'(1);
          end
        end

        S_DN_ISO: begin
          iso_en_q[dom_idx] <= 1'b1;
          if (cnt_q == CNT_W'(T_ISO - 1)) begin
            cnt_q <= '0;
            seq_q <= dom_switched ? S_DN_PWR : S_NEXT;
          end else begin
            cnt_q <= cnt_q + CNT_W'(1);
          end
        end

        S_DN_PWR: begin
          pwr_en_q[dom_idx] <= 1'b0;
          if (cnt_q == CNT_W'(T_SWITCH - 1)) begin
            cnt_q <= '0;
            seq_q <= S_NEXT;
          end else begin
            cnt_q <= cnt_q + CNT_W'(1);
          end
        end

        // ---- power up -------------------------------------------------
        S_UP_PWR: begin
          pwr_en_q[dom_idx] <= 1'b1;
          if ((dom_switched && ack_w[dom_idx]) ||
              (cnt_q == CNT_W'(T_SWITCH - 1))) begin
            cnt_q <= '0;
            seq_q <= dom_retentive ? S_UP_RESTORE : S_UP_ISO;
          end else begin
            cnt_q <= cnt_q + CNT_W'(1);
          end
        end

        S_UP_RESTORE: begin
          ret_restore_q[dom_idx] <= 1'b1;
          if (cnt_q == CNT_W'(T_RESTORE - 1)) begin
            ret_restore_q[dom_idx] <= 1'b0;
            cnt_q                  <= '0;
            seq_q                  <= S_UP_ISO;
          end else begin
            cnt_q <= cnt_q + CNT_W'(1);
          end
        end

        S_UP_ISO: begin
          iso_en_q[dom_idx] <= 1'b0;
          if (cnt_q == CNT_W'(T_ISO - 1)) begin
            cnt_q <= '0;
            seq_q <= S_UP_RST;
          end else begin
            cnt_q <= cnt_q + CNT_W'(1);
          end
        end

        S_UP_RST: begin
          rst_n_q[dom_idx] <= 1'b1;
          if (cnt_q == CNT_W'(T_RESET - 1)) begin
            cnt_q <= '0;
            seq_q <= S_UP_CLK;
          end else begin
            cnt_q <= cnt_q + CNT_W'(1);
          end
        end

        S_UP_CLK: begin
          clk_en_q[dom_idx] <= 1'b1;
          cnt_q             <= '0;
          seq_q             <= S_NEXT;
        end

        S_NEXT: begin
          on_q[dom_idx] <= want_bit;
          dom_q         <= dom_q + 4'd1;
          cnt_q         <= '0;
          seq_q         <= S_SCAN;
        end

        default: seq_q <= S_IDLE;
      endcase
    end
  end

endmodule : pmu_fsm
