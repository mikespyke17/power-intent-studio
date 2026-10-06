// ---------------------------------------------------------------------------
// apb_demux -- routes one APB requester to four completers using paddr[15:12].
// ---------------------------------------------------------------------------
module apb_demux
  import aura_pkg::*;
#(
  parameter int unsigned AW = 16
) (
  apb_if.cmp up,
  apb_if.req dn0,
  apb_if.req dn1,
  apb_if.req dn2,
  apb_if.req dn3
);

  logic [1:0] sel;
  assign sel = up.paddr[AW-3:AW-4];

  // Address, control and write data fan out unconditionally; only psel is
  // qualified, which is what an APB completer keys off.
  assign dn0.paddr = up.paddr; assign dn0.pprot = up.pprot;
  assign dn0.pwrite = up.pwrite; assign dn0.pwdata = up.pwdata;
  assign dn0.pstrb = up.pstrb; assign dn0.penable = up.penable;
  assign dn0.psel = up.psel & (sel == 2'd0);

  assign dn1.paddr = up.paddr; assign dn1.pprot = up.pprot;
  assign dn1.pwrite = up.pwrite; assign dn1.pwdata = up.pwdata;
  assign dn1.pstrb = up.pstrb; assign dn1.penable = up.penable;
  assign dn1.psel = up.psel & (sel == 2'd1);

  assign dn2.paddr = up.paddr; assign dn2.pprot = up.pprot;
  assign dn2.pwrite = up.pwrite; assign dn2.pwdata = up.pwdata;
  assign dn2.pstrb = up.pstrb; assign dn2.penable = up.penable;
  assign dn2.psel = up.psel & (sel == 2'd2);

  assign dn3.paddr = up.paddr; assign dn3.pprot = up.pprot;
  assign dn3.pwrite = up.pwrite; assign dn3.pwdata = up.pwdata;
  assign dn3.pstrb = up.pstrb; assign dn3.penable = up.penable;
  assign dn3.psel = up.psel & (sel == 2'd3);

  always_comb begin
    unique case (sel)
      2'd0: begin up.prdata = dn0.prdata; up.pready = dn0.pready; up.pslverr = dn0.pslverr; end
      2'd1: begin up.prdata = dn1.prdata; up.pready = dn1.pready; up.pslverr = dn1.pslverr; end
      2'd2: begin up.prdata = dn2.prdata; up.pready = dn2.pready; up.pslverr = dn2.pslverr; end
      2'd3: begin up.prdata = dn3.prdata; up.pready = dn3.pready; up.pslverr = dn3.pslverr; end
      default: begin up.prdata = '0; up.pready = 1'b1; up.pslverr = 1'b1; end
    endcase
  end

endmodule : apb_demux


// ---------------------------------------------------------------------------
// periph_subsys -- PD_PERIPH.  Header-switched, no retention.  All of its
// pad-facing outputs are isolated so the pads hold a defined level while the
// domain is collapsed.
// ---------------------------------------------------------------------------
module periph_subsys
  import aura_pkg::*;
#(
  parameter int unsigned APB_AW = 16,
  parameter int unsigned N_GPIO = 16
) (
  input  logic              clk,
  input  logic              rst_n,
  apb_if.cmp                apb,
  pmu_if.dom                pmu,

  input  logic              uart_rxd,
  output logic              uart_txd,
  output logic              spi_sclk,
  output logic              spi_mosi,
  input  logic              spi_miso,
  output logic              spi_csn,
  input  logic [N_GPIO-1:0] gpio_in,
  output logic [N_GPIO-1:0] gpio_out,
  output logic [N_GPIO-1:0] gpio_oe,
  output logic [3:0]        irq
);

  apb_if #(.AW(APB_AW)) uart_bus  (.clk(clk), .rst_n(rst_n));
  apb_if #(.AW(APB_AW)) spi_bus   (.clk(clk), .rst_n(rst_n));
  apb_if #(.AW(APB_AW)) timer_bus (.clk(clk), .rst_n(rst_n));
  apb_if #(.AW(APB_AW)) gpio_bus  (.clk(clk), .rst_n(rst_n));

  apb_demux #(.AW(APB_AW)) u_demux (
    .up  (apb),
    .dn0 (uart_bus),
    .dn1 (spi_bus),
    .dn2 (timer_bus),
    .dn3 (gpio_bus)
  );

  uart #(.FIFO_DEPTH(8)) u_uart (
    .apb (uart_bus),
    .rxd (uart_rxd),
    .txd (uart_txd),
    .irq (irq[0])
  );

  spi_master #(.FIFO_DEPTH(4)) u_spi (
    .apb  (spi_bus),
    .sclk (spi_sclk),
    .mosi (spi_mosi),
    .miso (spi_miso),
    .csn  (spi_csn),
    .irq  (irq[1])
  );

  timer_unit u_timer (
    .apb (timer_bus),
    .irq (irq[2])
  );

  gpio #(.N_IO(N_GPIO)) u_gpio (
    .apb      (gpio_bus),
    .gpio_in  (gpio_in),
    .gpio_out (gpio_out),
    .gpio_oe  (gpio_oe),
    .irq      (irq[3])
  );

  pwr_ack_gen #(.DELAY(4)) u_ack (
    .clk     (pmu.pclk),
    .rst_n   (pmu.prst_n),
    .pwr_en  (pmu.pwr_en),
    .pwr_ack (pmu.pwr_ack)
  );

endmodule : periph_subsys
