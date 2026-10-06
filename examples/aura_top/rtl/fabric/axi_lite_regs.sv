// ---------------------------------------------------------------------------
// axi_lite_regs -- generic AXI4-Lite register-file front end.
//
// Holds no storage of its own: it decodes the word address, emits a one-cycle
// write strobe, and muxes the caller's read-back array.  Every configuration
// block in the SoC reuses it.
// ---------------------------------------------------------------------------
module axi_lite_regs
  import aura_pkg::*;
  import aura_axi_pkg::*;
#(
  parameter int unsigned N_REG = 8,
  parameter int unsigned IDX_W = (N_REG <= 1) ? 1 : $clog2(N_REG)
) (
  axi_lite_if.slv           bus,
  output logic [N_REG-1:0]  wr_stb,
  output logic [DATA_W-1:0] wr_data,
  output logic [STRB_W-1:0] wr_strb,
  input  logic [DATA_W-1:0] rd_data [N_REG]
);

  // ---- write channel -------------------------------------------------------
  logic             wr_pend_q;
  logic             wr_err_q;
  logic [IDX_W-1:0] wr_idx;
  logic             wr_hit;
  logic             wr_fire;

  assign wr_idx  = bus.awaddr[IDX_W+1:2];
  assign wr_hit  = (bus.awaddr[ADDR_W-1:2] < ADDR_W'(N_REG));
  assign wr_fire = bus.awvalid & bus.wvalid & ~wr_pend_q;

  assign bus.awready = wr_fire;
  assign bus.wready  = wr_fire;
  assign bus.bvalid  = wr_pend_q;
  assign bus.bresp   = wr_err_q ? AXI_SLVERR : AXI_OKAY;

  assign wr_data = bus.wdata;
  assign wr_strb = bus.wstrb;

  always_comb begin
    wr_stb = '0;
    if (wr_fire && wr_hit) wr_stb[wr_idx] = 1'b1;
  end

  always_ff @(posedge bus.clk or negedge bus.rst_n) begin
    if (!bus.rst_n) begin
      wr_pend_q <= 1'b0;
      wr_err_q  <= 1'b0;
    end else if (wr_fire) begin
      wr_pend_q <= 1'b1;
      wr_err_q  <= ~wr_hit;
    end else if (wr_pend_q && bus.bready) begin
      wr_pend_q <= 1'b0;
    end
  end

  // ---- read channel --------------------------------------------------------
  logic             rd_pend_q;
  logic             rd_err_q;
  logic [IDX_W-1:0] rd_idx_q;
  logic [IDX_W-1:0] rd_idx;
  logic             rd_hit;
  logic             rd_fire;

  assign rd_idx  = bus.araddr[IDX_W+1:2];
  assign rd_hit  = (bus.araddr[ADDR_W-1:2] < ADDR_W'(N_REG));
  assign rd_fire = bus.arvalid & ~rd_pend_q;

  assign bus.arready = rd_fire;
  assign bus.rvalid  = rd_pend_q;
  assign bus.rresp   = rd_err_q ? AXI_SLVERR : AXI_OKAY;
  assign bus.rdata   = rd_err_q ? '0 : rd_data[rd_idx_q];

  always_ff @(posedge bus.clk or negedge bus.rst_n) begin
    if (!bus.rst_n) begin
      rd_pend_q <= 1'b0;
      rd_err_q  <= 1'b0;
      rd_idx_q  <= '0;
    end else if (rd_fire) begin
      rd_pend_q <= 1'b1;
      rd_err_q  <= ~rd_hit;
      rd_idx_q  <= rd_hit ? rd_idx : '0;
    end else if (rd_pend_q && bus.rready) begin
      rd_pend_q <= 1'b0;
    end
  end

endmodule : axi_lite_regs
