// ---------------------------------------------------------------------------
// mac_array -- N_MAC parallel MAC lanes sharing one broadcast sample stream.
// ---------------------------------------------------------------------------
module mac_array
  import aura_dsp_pkg::*;
(
  input  logic                   clk,
  input  logic                   rst_n,
  input  logic                   clear,
  input  logic                   en,
  input  samp_t                  sample,
  input  logic                   coeff_we,
  input  logic [LANE_SEL_W-1:0]  coeff_lane,
  input  coef_t                  coeff_data,
  output samp_t                  lane_result [N_MAC],
  output acc_t                   sum
);

  acc_t lane_acc [N_MAC];

  for (genvar l = 0; l < N_MAC; l++) begin : g_lane
    mac_unit #(.LANE(l)) u_mac (
      .clk      (clk),
      .rst_n    (rst_n),
      .clear    (clear),
      .en       (en),
      .sample   (sample),
      .coeff    (coeff_data),
      .coeff_we (coeff_we && (coeff_lane == LANE_SEL_W'(l))),
      .acc      (lane_acc[l]),
      .result   (lane_result[l])
    );
  end

  always_comb begin
    sum = '0;
    for (int unsigned l = 0; l < N_MAC; l++) sum = sum + lane_acc[l];
  end

endmodule : mac_array


// ---------------------------------------------------------------------------
// dsp_mem -- dual-port sample buffer built from a hard macro.  Port A is
// written by the fabric, port B streams samples to the sequencer.
// ---------------------------------------------------------------------------
module dsp_mem
  import aura_pkg::*;
  import aura_dsp_pkg::*;
(
  sram_if.mem                  wr_bus,
  input  logic                 rd_req,
  input  logic [DSP_MEM_AW-1:0] rd_addr,
  output logic [DATA_W-1:0]    rd_data,
  output logic                 rd_valid,
  input  logic                 ret_n,
  input  logic                 pd_n
);

  sram_2rw_wrap #(
    .WORDS (DSP_MEM_DEPTH),
    .BITS  (DATA_W),
    .AW    (DSP_MEM_AW)
  ) u_macro_wrap (
    .bus_a    (wr_bus),
    .b_req    (rd_req),
    .b_addr   (rd_addr),
    .b_rdata  (rd_data),
    .b_rvalid (rd_valid),
    .ret_n    (ret_n),
    .pd_n     (pd_n)
  );

endmodule : dsp_mem


// ---------------------------------------------------------------------------
// dsp_seq_ctrl -- streams samples out of the buffer, feeds the FIR and the MAC
// array, and reports completion.
// ---------------------------------------------------------------------------
module dsp_seq_ctrl
  import aura_pkg::*;
  import aura_dsp_pkg::*;
(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic                  start,
  input  logic [DSP_MEM_AW-1:0] base_addr,
  input  logic [DSP_MEM_AW-1:0] length,

  output logic                  rd_req,
  output logic [DSP_MEM_AW-1:0] rd_addr,
  input  logic [DATA_W-1:0]     rd_data,
  input  logic                  rd_valid,

  output logic                  samp_valid,
  output samp_t                 samp_data,
  output logic                  clear,
  output dsp_state_e            state,
  output logic                  done
);

  dsp_state_e            st_q;
  logic [DSP_MEM_AW-1:0] ptr_q;
  logic [DSP_MEM_AW-1:0] cnt_q;
  logic                  done_q;

  assign rd_req     = (st_q == DSP_RUN);
  assign rd_addr    = ptr_q;
  assign samp_valid = rd_valid & (st_q == DSP_RUN);
  assign samp_data  = samp_t'(rd_data[SAMP_W-1:0]);
  assign clear      = (st_q == DSP_LOAD);
  assign state      = st_q;
  assign done       = done_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      st_q   <= DSP_IDLE;
      ptr_q  <= '0;
      cnt_q  <= '0;
      done_q <= 1'b0;
    end else begin
      unique case (st_q)
        DSP_IDLE: begin
          done_q <= 1'b0;
          if (start) begin
            ptr_q <= base_addr;
            cnt_q <= length;
            st_q  <= DSP_LOAD;
          end
        end

        DSP_LOAD: st_q <= (cnt_q == '0) ? DSP_DONE : DSP_RUN;

        DSP_RUN: begin
          ptr_q <= ptr_q + DSP_MEM_AW'(1);
          if (cnt_q == DSP_MEM_AW'(1)) begin
            st_q <= DSP_DONE;
          end else begin
            cnt_q <= cnt_q - DSP_MEM_AW'(1);
          end
        end

        DSP_DONE: begin
          done_q <= 1'b1;
          st_q   <= DSP_IDLE;
        end

        default: st_q <= DSP_IDLE;
      endcase
    end
  end

endmodule : dsp_seq_ctrl


// ---------------------------------------------------------------------------
// dsp_subsys -- PD_DSP.  Header-switched with no retention: the whole block is
// discarded on power-down and reloaded by software.  It runs from the boost
// rail VDD_HI, so its outputs need high-to-low level shifters and its inputs
// need low-to-high ones.
//
// Register map (AXI4-Lite, word addressed):
//   0x00 CTRL    [0] start, [1] irq clear
//   0x04 STATUS  [1:0] state, [8] done
//   0x08 BASE
//   0x0C LENGTH
//   0x10 COEF    [15:0] value, [23:16] lane, [24] MAC write, [25] FIR write
//   0x14 RESULT  FIR output sample
//   0x18 SUM     MAC array accumulator, low word
//   0x1C SUMH    MAC array accumulator, high word
// ---------------------------------------------------------------------------
module dsp_subsys
  import aura_pkg::*;
  import aura_dsp_pkg::*;
#(
  parameter int unsigned N_REG = 8
) (
  input  logic     clk,
  input  logic     rst_n,
  axi_lite_if.slv  cfg,        // configuration and sample-buffer writes
  pmu_if.dom       pmu,
  output logic     dsp_irq
);

  // ---- registers -----------------------------------------------------------
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

  logic [DATA_W-1:0] reg_base_q;
  logic [DATA_W-1:0] reg_len_q;
  logic              start_q;
  logic              irq_q;

  // ---- sample memory -------------------------------------------------------
  sram_if #(.AW(DSP_MEM_AW), .DW(DATA_W)) mem_wr (.clk(clk), .rst_n(rst_n));

  logic                  rd_req;
  logic [DSP_MEM_AW-1:0] rd_addr;
  logic [DATA_W-1:0]     rd_data_mem;
  logic                  rd_valid;
  logic                  mem_ret_n;
  logic                  mem_pd_n;

  // PD_DSP has no retention strategy: the array is simply powered down.
  assign mem_ret_n = 1'b1;
  assign mem_pd_n  = pmu.pwr_en;

  // Sample-buffer writes are the register block's aliased upper window.
  assign mem_wr.req   = wr_stb[2] & wr_data[31];
  assign mem_wr.we    = 1'b1;
  assign mem_wr.addr  = reg_base_q[DSP_MEM_AW-1:0];
  assign mem_wr.wdata = wr_data;
  assign mem_wr.be    = wr_strb;

  dsp_mem u_mem (
    .wr_bus   (mem_wr),
    .rd_req   (rd_req),
    .rd_addr  (rd_addr),
    .rd_data  (rd_data_mem),
    .rd_valid (rd_valid),
    .ret_n    (mem_ret_n),
    .pd_n     (mem_pd_n)
  );

  // ---- sequencer -----------------------------------------------------------
  logic       samp_valid;
  samp_t      samp_data;
  logic       clear;
  dsp_state_e state;
  logic       seq_done;

  dsp_seq_ctrl u_seq (
    .clk        (clk),
    .rst_n      (rst_n),
    .start      (start_q),
    .base_addr  (reg_base_q[DSP_MEM_AW-1:0]),
    .length     (reg_len_q[DSP_MEM_AW-1:0]),
    .rd_req     (rd_req),
    .rd_addr    (rd_addr),
    .rd_data    (rd_data_mem),
    .rd_valid   (rd_valid),
    .samp_valid (samp_valid),
    .samp_data  (samp_data),
    .clear      (clear),
    .state      (state),
    .done       (seq_done)
  );

  // ---- compute -------------------------------------------------------------
  samp_t fir_out;
  logic  fir_valid;
  samp_t lane_result [N_MAC];
  acc_t  mac_sum;

  fir_filter u_fir (
    .clk        (clk),
    .rst_n      (rst_n),
    .clear      (clear),
    .in_valid   (samp_valid),
    .in_sample  (samp_data),
    .out_valid  (fir_valid),
    .out_sample (fir_out),
    .tap_we     (wr_stb[4] & wr_data[25]),
    .tap_addr   (wr_data[16 +: TAP_SEL_W]),
    .tap_data   (coef_t'(wr_data[COEF_W-1:0]))
  );

  mac_array u_macs (
    .clk         (clk),
    .rst_n       (rst_n),
    .clear       (clear),
    .en          (fir_valid),
    .sample      (fir_out),
    .coeff_we    (wr_stb[4] & wr_data[24]),
    .coeff_lane  (wr_data[16 +: LANE_SEL_W]),
    .coeff_data  (coef_t'(wr_data[COEF_W-1:0])),
    .lane_result (lane_result),
    .sum         (mac_sum)
  );

  samp_t result_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      reg_base_q <= '0;
      reg_len_q  <= '0;
      start_q    <= 1'b0;
      irq_q      <= 1'b0;
      result_q   <= '0;
    end else begin
      start_q <= 1'b0;
      if (wr_stb[0]) begin
        start_q <= wr_data[0];
        if (wr_data[1]) irq_q <= 1'b0;
      end
      if (wr_stb[2]) reg_base_q <= wr_data;
      if (wr_stb[3]) reg_len_q  <= wr_data;
      if (fir_valid) result_q   <= fir_out;
      if (seq_done)  irq_q      <= 1'b1;
    end
  end

  assign dsp_irq = irq_q;

  always_comb begin
    for (int unsigned r = 0; r < N_REG; r++) rd_data[r] = '0;
    rd_data[0] = {31'd0, start_q};
    rd_data[1] = {23'd0, irq_q, 6'd0, state};
    rd_data[2] = reg_base_q;
    rd_data[3] = reg_len_q;
    rd_data[4] = {{(DATA_W-SAMP_W){1'b0}}, lane_result[0]};
    rd_data[5] = {{(DATA_W-SAMP_W){result_q[SAMP_W-1]}}, result_q};
    rd_data[6] = mac_sum[DATA_W-1:0];
    rd_data[7] = {{(2*DATA_W-ACC_W){mac_sum[ACC_W-1]}}, mac_sum} >> DATA_W;
  end

  // ---- power handshake -----------------------------------------------------
  pwr_ack_gen #(.DELAY(10)) u_ack (
    .clk     (pmu.pclk),
    .rst_n   (pmu.prst_n),
    .pwr_en  (pmu.pwr_en),
    .pwr_ack (pmu.pwr_ack)
  );

endmodule : dsp_subsys
