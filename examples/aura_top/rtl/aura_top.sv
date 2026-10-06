// ---------------------------------------------------------------------------
// aura_top -- AURA edge-sensor SoC.
//
// Power partitioning (see upf/aura_top.upf):
//
//   PD_TOP     VDD_TOP 0.80          always on   fabric, clock/reset root
//   PD_AON     VDD_AON 0.80 / 0.60   always on   PMU, RTC, wake
//   PD_CPU     VDD_CPU (switched)    retention   core, icache
//   PD_DSP     VDD_DSP (switched)    none        MAC array, FIR, sample buffer
//   PD_MEM     VDD_MEM 0.80 / 0.60   retention   SRAM banks
//   PD_PERIPH  VDD_PER (switched)    none        UART, SPI, timer, GPIO
// ---------------------------------------------------------------------------
module aura_top
  import aura_pkg::*;
  import aura_pmu_pkg::*;
#(
  parameter int unsigned N_GPIO = 16,
  parameter int unsigned N_WAKE = 4,
  parameter int unsigned APB_AW = 16
) (
  input  logic              clk_ref,
  input  logic              rst_ext_n,

  input  logic [N_WAKE-1:0] wake_pin,

  input  logic              uart_rxd,
  output logic              uart_txd,
  output logic              spi_sclk,
  output logic              spi_mosi,
  input  logic              spi_miso,
  output logic              spi_csn,
  input  logic [N_GPIO-1:0] gpio_in,
  output logic [N_GPIO-1:0] gpio_out,
  output logic [N_GPIO-1:0] gpio_oe,

  output logic              irq_o,
  output logic              wake_o
);

  // -------------------------------------------------------------------------
  // Clocks and resets
  //
  // Every domain clock is a gated copy of the single root clk_ref -- there is
  // no divider or second oscillator anywhere in the design.  Crossings between
  // domains are therefore synchronous and need no CDC synchronisers; what they
  // do need is for the destination clock to be running, which is why the PMU
  // turns clocks off last on the way down and on first on the way up.
  // -------------------------------------------------------------------------
  logic [N_DOMAIN-1:0] clk_dom;
  logic [N_DOMAIN-1:0] rst_dom_n;
  logic [N_DOMAIN-1:0] clk_en_w;
  logic [N_DOMAIN-1:0] rst_req_n_w;

  logic clk_aon;
  logic rst_aon_n;
  logic clk_top;
  logic rst_top_n;

  clk_rst_gen #(.N_DOM(N_DOMAIN)) u_clk_rst (
    .clk_ref   (clk_ref),
    .rst_ext_n (rst_ext_n),
    .clk_en    (clk_en_w),
    .rst_req_n (rst_req_n_w),
    .clk_dom   (clk_dom),
    .rst_dom_n (rst_dom_n)
  );

  assign clk_aon   = clk_dom[DOM_AON];
  assign rst_aon_n = rst_dom_n[DOM_AON];
  assign clk_top   = clk_dom[DOM_TOP];
  assign rst_top_n = rst_dom_n[DOM_TOP];

  // -------------------------------------------------------------------------
  // Power control channels, one per domain
  // -------------------------------------------------------------------------
  pmu_if pmu_ch [N_DOMAIN] (.pclk(clk_aon), .prst_n(rst_aon_n));

  // Flat taps of the channel signals.  The UPF isolation, retention and power
  // switch strategies reference these nets by name, so they must exist at this
  // level of hierarchy and must not be optimised away.
  (* keep = "true" *) logic [N_DOMAIN-1:0] pwr_en_w;
  (* keep = "true" *) logic [N_DOMAIN-1:0] pwr_ack_w;
  (* keep = "true" *) logic [N_DOMAIN-1:0] iso_en_w;
  (* keep = "true" *) logic [N_DOMAIN-1:0] ret_save_w;
  (* keep = "true" *) logic [N_DOMAIN-1:0] ret_restore_w;

  for (genvar d = 0; d < N_DOMAIN; d++) begin : g_pmu_tap
    assign pwr_en_w[d]      = pmu_ch[d].pwr_en;
    assign pwr_ack_w[d]     = pmu_ch[d].pwr_ack;
    assign iso_en_w[d]      = pmu_ch[d].iso_en;
    assign ret_save_w[d]    = pmu_ch[d].ret_save;
    assign ret_restore_w[d] = pmu_ch[d].ret_restore;
  end

  // -------------------------------------------------------------------------
  // Buses
  // -------------------------------------------------------------------------
  axi_lite_if axi_mst [N_AXI_MST] (.clk(clk_top), .rst_n(rst_top_n));
  axi_lite_if axi_slv [N_AXI_SLV] (.clk(clk_top), .rst_n(rst_top_n));
  apb_if #(.AW(APB_AW)) apb_bus   (.clk(clk_top), .rst_n(rst_top_n));

  // -------------------------------------------------------------------------
  // PD_TOP : interconnect
  // -------------------------------------------------------------------------
  fabric_subsys #(
    .N_M    (N_AXI_MST),
    .N_S    (N_AXI_SLV),
    .APB_AW (APB_AW)
  ) u_fabric (
    .clk      (clk_top),
    .rst_n    (rst_top_n),
    .mst_port (axi_mst),
    .slv_port (axi_slv),
    .apb      (apb_bus)
  );

  // -------------------------------------------------------------------------
  // PD_AON : power management
  // -------------------------------------------------------------------------
  power_state_e cur_state;
  logic         wake_req;

  aon_subsys #(
    .N_DOM  (N_DOMAIN),
    .N_WAKE (N_WAKE)
  ) u_aon (
    .clk_aon     (clk_aon),
    .rst_aon_n   (rst_aon_n),
    .wake_pin    (wake_pin),
    .cfg         (axi_slv[SLV_AON]),
    .ch          (pmu_ch),
    .clk_en_o    (clk_en_w),
    .rst_req_n_o (rst_req_n_w),
    .wake_req_o  (wake_req),
    .cur_state_o (cur_state)
  );

  assign wake_o = wake_req;

  // -------------------------------------------------------------------------
  // PD_CPU
  // -------------------------------------------------------------------------
  logic cpu_illegal;
  logic cpu_active;

  cpu_subsys #(
    .RESET_PC (32'h0000_0000)
  ) u_cpu (
    .clk           (clk_dom[DOM_CPU]),
    .rst_n         (rst_dom_n[DOM_CPU]),
    .mem_port      (axi_mst[MST_CPU]),
    .pmu           (pmu_ch[DOM_CPU]),
    .illegal_instr (cpu_illegal),
    .core_active   (cpu_active)
  );

  // -------------------------------------------------------------------------
  // PD_DSP
  // -------------------------------------------------------------------------
  logic dsp_irq;

  dsp_subsys u_dsp (
    .clk     (clk_dom[DOM_DSP]),
    .rst_n   (rst_dom_n[DOM_DSP]),
    .cfg     (axi_slv[SLV_DSP]),
    .pmu     (pmu_ch[DOM_DSP]),
    .dsp_irq (dsp_irq)
  );

  // The DSP is a fabric initiator only for its sample buffer, which is written
  // through the configuration port, so its master slot idles.
  assign axi_mst[MST_DSP].awaddr  = '0;
  assign axi_mst[MST_DSP].awprot  = 3'b000;
  assign axi_mst[MST_DSP].awvalid = 1'b0;
  assign axi_mst[MST_DSP].wdata   = '0;
  assign axi_mst[MST_DSP].wstrb   = '0;
  assign axi_mst[MST_DSP].wvalid  = 1'b0;
  assign axi_mst[MST_DSP].bready  = 1'b1;
  assign axi_mst[MST_DSP].araddr  = '0;
  assign axi_mst[MST_DSP].arprot  = 3'b000;
  assign axi_mst[MST_DSP].arvalid = 1'b0;
  assign axi_mst[MST_DSP].rready  = 1'b1;

  // -------------------------------------------------------------------------
  // PD_MEM
  // -------------------------------------------------------------------------
  mem_subsys #(
    .N_BANK (N_SRAM_BANK),
    .WORDS  (SRAM_DEPTH),
    .AW     (SRAM_AW)
  ) u_mem (
    .clk   (clk_dom[DOM_MEM]),
    .rst_n (rst_dom_n[DOM_MEM]),
    .bus   (axi_slv[SLV_MEM]),
    .pmu   (pmu_ch[DOM_MEM])
  );

  // -------------------------------------------------------------------------
  // PD_PERIPH
  // -------------------------------------------------------------------------
  logic [3:0] per_irq;

  periph_subsys #(
    .APB_AW (APB_AW),
    .N_GPIO (N_GPIO)
  ) u_periph (
    .clk       (clk_dom[DOM_PER]),
    .rst_n     (rst_dom_n[DOM_PER]),
    .apb       (apb_bus),
    .pmu       (pmu_ch[DOM_PER]),
    .uart_rxd  (uart_rxd),
    .uart_txd  (uart_txd),
    .spi_sclk  (spi_sclk),
    .spi_mosi  (spi_mosi),
    .spi_miso  (spi_miso),
    .spi_csn   (spi_csn),
    .gpio_in   (gpio_in),
    .gpio_out  (gpio_out),
    .gpio_oe   (gpio_oe),
    .irq       (per_irq)
  );

  // -------------------------------------------------------------------------
  // Interrupt aggregation (PD_TOP)
  // -------------------------------------------------------------------------
  assign irq_o = |per_irq | dsp_irq | cpu_illegal;

  // PD_TOP and PD_AON never leave the on state, so their acknowledges track
  // their enables directly.
  assign pmu_ch[DOM_TOP].pwr_ack = pmu_ch[DOM_TOP].pwr_en;
  assign pmu_ch[DOM_AON].pwr_ack = pmu_ch[DOM_AON].pwr_en;

endmodule : aura_top
