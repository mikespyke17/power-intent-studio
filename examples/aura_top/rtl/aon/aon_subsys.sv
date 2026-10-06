// ---------------------------------------------------------------------------
// aon_subsys -- PD_AON.  Never powered down; runs from VDD_AON, which drops to
// its low rail in PS_DEEP_SLEEP.  Everything leaving this block crosses into a
// higher-voltage domain, so every output is a low-to-high level shifter
// candidate in the UPF.
//
// Register map (AXI4-Lite, word addressed):
//   0x00 PMU_CTRL    [2:0] target state, [8] request
//   0x04 PMU_STATUS  [2:0] current state, [8] busy, [21:16] domain on mask
//   0x08 RTC_CTRL    [0] enable, [1] alarm enable
//   0x0C RTC_COUNT   read only
//   0x10 RTC_ALARM
//   0x14 WAKE_MASK
//   0x18 WAKE_STATUS read, write-1-to-clear
//   0x1C DOM_ACK     read only, per-domain switch acknowledge
// ---------------------------------------------------------------------------
module aon_subsys
  import aura_pkg::*;
  import aura_pmu_pkg::*;
#(
  parameter int unsigned N_DOM  = aura_pkg::N_DOMAIN,
  parameter int unsigned N_WAKE = 4,
  parameter int unsigned N_REG  = 8
) (
  input  logic              clk_aon,
  input  logic              rst_aon_n,
  input  logic [N_WAKE-1:0] wake_pin,

  axi_lite_if.slv           cfg,
  pmu_if.pmu                ch [N_DOM],

  output logic [N_DOM-1:0]  clk_en_o,
  output logic [N_DOM-1:0]  rst_req_n_o,
  output logic              wake_req_o,
  output power_state_e      cur_state_o
);

  // ---- register storage ----------------------------------------------------
  logic [DATA_W-1:0] reg_ctrl_q;
  logic [DATA_W-1:0] reg_rtc_ctrl_q;
  logic [DATA_W-1:0] reg_rtc_alarm_q;
  logic [DATA_W-1:0] reg_wake_mask_q;
  logic              req_pulse_q;

  logic [N_REG-1:0]  wr_stb;
  logic [DATA_W-1:0] wr_data;
  logic [STRB_W-1:0] wr_strb;
  logic [DATA_W-1:0] rd_data [N_REG];

  axi_lite_regs #(.N_REG(N_REG)) u_regs (
    .bus     (cfg),
    .wr_stb  (wr_stb),
    .wr_data (wr_data),
    .wr_strb (wr_strb),
    .rd_data (rd_data)
  );

  // ---- sequencer -----------------------------------------------------------
  power_state_e      cur_state;
  logic              busy;
  logic [N_DOM-1:0]  dom_on;
  logic [N_DOM-1:0]  clk_en_w;
  logic [N_DOM-1:0]  rst_n_w;
  logic [N_DOM-1:0]  ack_w;

  pmu_fsm #(.N_DOM(N_DOM)) u_seq (
    .clk_aon   (clk_aon),
    .rst_aon_n (rst_aon_n),
    .req_state (power_state_e'(reg_ctrl_q[2:0])),
    .req_valid (req_pulse_q),
    .cur_state (cur_state),
    .busy      (busy),
    .dom_on    (dom_on),
    .ch        (ch)
  );

  // Mirror the channel controls that the clock/reset generator needs.
  for (genvar g = 0; g < N_DOM; g++) begin : g_tap
    assign clk_en_w[g] = ch[g].clk_en;
    assign rst_n_w[g]  = ch[g].rst_n;
    assign ack_w[g]    = ch[g].pwr_ack;
  end

  assign clk_en_o    = clk_en_w;
  assign rst_req_n_o = rst_n_w;
  assign cur_state_o = cur_state;

  // ---- RTC and wake --------------------------------------------------------
  logic [31:0]     rtc_count;
  logic            rtc_tick;
  logic            rtc_alarm;
  logic [N_WAKE:0] wake_src;
  logic            wake_req;
  logic            wake_clear;

  rtc_timer #(.PRESCALE(32), .CNT_W(32)) u_rtc (
    .clk_aon   (clk_aon),
    .rst_aon_n (rst_aon_n),
    .enable    (reg_rtc_ctrl_q[0]),
    .alarm_val (reg_rtc_alarm_q),
    .alarm_en  (reg_rtc_ctrl_q[1]),
    .count     (rtc_count),
    .tick      (rtc_tick),
    .alarm     (rtc_alarm)
  );

  assign wake_clear = wr_stb[6] & wr_data[0];

  wake_ctrl #(.N_WAKE(N_WAKE)) u_wake (
    .clk_aon   (clk_aon),
    .rst_aon_n (rst_aon_n),
    .wake_pin  (wake_pin),
    .wake_mask (reg_wake_mask_q[N_WAKE-1:0]),
    .rtc_alarm (rtc_alarm),
    .clear     (wake_clear),
    .wake_src  (wake_src),
    .wake_req  (wake_req)
  );

  assign wake_req_o = wake_req;

  // ---- register write / read-back -----------------------------------------
  function automatic logic [DATA_W-1:0] wr_merge(input logic [DATA_W-1:0] old_v);
    logic [DATA_W-1:0] res;
    res = old_v;
    for (int unsigned b = 0; b < STRB_W; b++) begin
      if (wr_strb[b]) res[8*b +: 8] = wr_data[8*b +: 8];
    end
    return res;
  endfunction

  always_ff @(posedge clk_aon or negedge rst_aon_n) begin
    if (!rst_aon_n) begin
      reg_ctrl_q      <= {29'd0, PS_SHUTDOWN};
      reg_rtc_ctrl_q  <= 32'h0000_0001;
      reg_rtc_alarm_q <= '0;
      reg_wake_mask_q <= {{(DATA_W-N_WAKE){1'b0}}, {N_WAKE{1'b1}}};
      req_pulse_q     <= 1'b0;
    end else begin
      req_pulse_q <= 1'b0;
      if (wr_stb[0]) begin
        reg_ctrl_q  <= wr_merge(reg_ctrl_q);
        req_pulse_q <= wr_data[8];
      end
      if (wr_stb[2]) reg_rtc_ctrl_q  <= wr_merge(reg_rtc_ctrl_q);
      if (wr_stb[4]) reg_rtc_alarm_q <= wr_merge(reg_rtc_alarm_q);
      if (wr_stb[5]) reg_wake_mask_q <= wr_merge(reg_wake_mask_q);

      // A wake event automatically requests the nominal running state.
      if (wake_req && (cur_state != PS_NOM) && !busy) begin
        reg_ctrl_q[2:0] <= PS_NOM;
        req_pulse_q     <= 1'b1;
      end
    end
  end

  always_comb begin
    for (int unsigned r = 0; r < N_REG; r++) rd_data[r] = '0;
    rd_data[0] = reg_ctrl_q;
    rd_data[1] = {10'd0, dom_on, 7'd0, busy, 5'd0, cur_state};
    rd_data[2] = reg_rtc_ctrl_q;
    rd_data[3] = rtc_count;
    rd_data[4] = reg_rtc_alarm_q;
    rd_data[5] = reg_wake_mask_q;
    rd_data[6] = {{(DATA_W-N_WAKE-1){1'b0}}, wake_src};
    rd_data[7] = {{(DATA_W-N_DOM){1'b0}}, ack_w};
  end

endmodule : aon_subsys
