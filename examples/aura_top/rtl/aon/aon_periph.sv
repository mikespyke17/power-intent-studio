// ---------------------------------------------------------------------------
// rtc_timer -- always-on real time counter with a compare alarm.
// ---------------------------------------------------------------------------
module rtc_timer #(
  parameter int unsigned PRESCALE = 32,
  parameter int unsigned CNT_W    = 32
) (
  input  logic              clk_aon,
  input  logic              rst_aon_n,
  input  logic              enable,
  input  logic [CNT_W-1:0]  alarm_val,
  input  logic              alarm_en,
  output logic [CNT_W-1:0]  count,
  output logic              tick,
  output logic              alarm
);

  localparam int unsigned PS_W = (PRESCALE <= 2) ? 1 : $clog2(PRESCALE);

  logic [PS_W-1:0]  ps_q;
  logic [CNT_W-1:0] cnt_q;
  logic             tick_q;
  logic             alarm_q;

  always_ff @(posedge clk_aon or negedge rst_aon_n) begin
    if (!rst_aon_n) begin
      ps_q    <= '0;
      cnt_q   <= '0;
      tick_q  <= 1'b0;
      alarm_q <= 1'b0;
    end else begin
      tick_q <= 1'b0;
      if (enable) begin
        if (ps_q == PS_W'(PRESCALE - 1)) begin
          ps_q   <= '0;
          cnt_q  <= cnt_q + CNT_W'(1);
          tick_q <= 1'b1;
          if (alarm_en && ((cnt_q + CNT_W'(1)) == alarm_val)) begin
            alarm_q <= 1'b1;
          end
        end else begin
          ps_q <= ps_q + PS_W'(1);
        end
      end
      if (!alarm_en) alarm_q <= 1'b0;
    end
  end

  assign count = cnt_q;
  assign tick  = tick_q;
  assign alarm = alarm_q;

endmodule : rtc_timer


// ---------------------------------------------------------------------------
// wake_ctrl -- double-synchronises the external wake pins, latches the wake
//              source and asks the PMU for a wake-up power state.
// ---------------------------------------------------------------------------
module wake_ctrl
  import aura_pkg::*;
#(
  parameter int unsigned N_WAKE = 4
) (
  input  logic              clk_aon,
  input  logic              rst_aon_n,
  input  logic [N_WAKE-1:0] wake_pin,
  input  logic [N_WAKE-1:0] wake_mask,
  input  logic              rtc_alarm,
  input  logic              clear,
  output logic [N_WAKE:0]   wake_src,
  output logic              wake_req
);

  logic [N_WAKE-1:0] sync0_q;
  logic [N_WAKE-1:0] sync1_q;
  logic [N_WAKE-1:0] sync2_q;
  logic [N_WAKE:0]   src_q;

  always_ff @(posedge clk_aon or negedge rst_aon_n) begin
    if (!rst_aon_n) begin
      sync0_q <= '0;
      sync1_q <= '0;
      sync2_q <= '0;
      src_q   <= '0;
    end else begin
      sync0_q <= wake_pin;
      sync1_q <= sync0_q;
      sync2_q <= sync1_q;
      if (clear) begin
        src_q <= '0;
      end else begin
        // rising edge on an unmasked pin, or the RTC alarm
        src_q[N_WAKE-1:0] <= src_q[N_WAKE-1:0] | (sync1_q & ~sync2_q & wake_mask);
        src_q[N_WAKE]     <= src_q[N_WAKE] | rtc_alarm;
      end
    end
  end

  assign wake_src = src_q;
  assign wake_req = |src_q;

endmodule : wake_ctrl


// ---------------------------------------------------------------------------
// clk_rst_gen -- root clock gating and reset synchronisation.
//                One gate per domain, driven by the PMU clk_en signals.
// ---------------------------------------------------------------------------
module clk_rst_gen #(
  parameter int unsigned N_DOM = aura_pkg::N_DOMAIN
) (
  input  logic             clk_ref,
  input  logic             rst_ext_n,
  input  logic [N_DOM-1:0] clk_en,
  input  logic [N_DOM-1:0] rst_req_n,
  output logic [N_DOM-1:0] clk_dom,
  output logic [N_DOM-1:0] rst_dom_n
);

  logic [1:0] sync_q;
  logic       rst_sync_n;

  always_ff @(posedge clk_ref or negedge rst_ext_n) begin
    if (!rst_ext_n) sync_q <= 2'b00;
    else            sync_q <= {sync_q[0], 1'b1};
  end
  assign rst_sync_n = sync_q[1];

  for (genvar d = 0; d < N_DOM; d++) begin : g_dom
    logic en_latch;

    // Standard latch-based integrated clock gate.
    always_latch begin
      if (!clk_ref) en_latch = clk_en[d];
    end

    assign clk_dom[d] = clk_ref & en_latch;

    logic [1:0] rsync_q;
    always_ff @(posedge clk_ref or negedge rst_sync_n) begin
      if (!rst_sync_n) rsync_q <= 2'b00;
      else             rsync_q <= {rsync_q[0], rst_req_n[d]};
    end
    assign rst_dom_n[d] = rsync_q[1];
  end

endmodule : clk_rst_gen
