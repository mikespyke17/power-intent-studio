// ---------------------------------------------------------------------------
// sram_bank -- one addressable bank wrapping a single-port hard macro.
// ---------------------------------------------------------------------------
module sram_bank
  import aura_pkg::*;
#(
  parameter int unsigned WORDS = aura_pkg::SRAM_DEPTH,
  parameter int unsigned AW    = $clog2(WORDS)
) (
  input  logic              clk,
  input  logic              rst_n,
  input  logic              sel,
  input  logic              req,
  input  logic              we,
  input  logic [AW-1:0]     addr,
  input  logic [DATA_W-1:0] wdata,
  input  logic [STRB_W-1:0] be,
  output logic [DATA_W-1:0] rdata,
  output logic              rvalid,
  input  logic              ret_n,
  input  logic              pd_n
);

  sram_if #(.AW(AW), .DW(DATA_W)) bus (.clk(clk), .rst_n(rst_n));

  assign bus.req   = sel & req;
  assign bus.we    = we;
  assign bus.addr  = addr;
  assign bus.wdata = wdata;
  assign bus.be    = be;

  assign rdata  = bus.rdata;
  assign rvalid = bus.rvalid;

  sram_1rw_wrap #(
    .WORDS (WORDS),
    .BITS  (DATA_W),
    .AW    (AW)
  ) u_wrap (
    .bus   (bus),
    .ret_n (ret_n),
    .pd_n  (pd_n)
  );

endmodule : sram_bank


// ---------------------------------------------------------------------------
// mem_ctrl -- AXI4-Lite slave front end for the banked SRAM.
// ---------------------------------------------------------------------------
module mem_ctrl
  import aura_pkg::*;
  import aura_axi_pkg::*;
#(
  parameter int unsigned N_BANK = aura_pkg::N_SRAM_BANK,
  parameter int unsigned BANK_W = (N_BANK <= 1) ? 1 : $clog2(N_BANK),
  parameter int unsigned WORD_AW = aura_pkg::SRAM_AW
) (
  axi_lite_if.slv           bus,
  output logic [N_BANK-1:0] bank_sel,
  output logic              bank_req,
  output logic              bank_we,
  output logic [WORD_AW-1:0] bank_addr,
  output logic [DATA_W-1:0] bank_wdata,
  output logic [STRB_W-1:0] bank_be,
  input  logic [DATA_W-1:0] bank_rdata [N_BANK],
  input  logic [N_BANK-1:0] bank_rvalid
);

  typedef enum logic [2:0] { M_IDLE, M_WR, M_BRESP, M_RD, M_RWAIT, M_RRESP } mst_e;

  mst_e              st_q;
  logic [WORD_AW-1:0] addr_q;
  logic [BANK_W-1:0] sel_q;
  logic [DATA_W-1:0] wdata_q;
  logic [STRB_W-1:0] strb_q;
  logic [DATA_W-1:0] rdata_q;

  logic wr_fire;
  logic rd_fire;

  assign wr_fire = (st_q == M_IDLE) & bus.awvalid & bus.wvalid;
  assign rd_fire = (st_q == M_IDLE) & bus.arvalid & ~(bus.awvalid & bus.wvalid);

  assign bus.awready = wr_fire;
  assign bus.wready  = wr_fire;
  assign bus.arready = rd_fire;
  assign bus.bvalid  = (st_q == M_BRESP);
  assign bus.bresp   = AXI_OKAY;
  assign bus.rvalid  = (st_q == M_RRESP);
  assign bus.rdata   = rdata_q;
  assign bus.rresp   = AXI_OKAY;

  always_comb begin
    bank_sel = '0;
    bank_sel[sel_q] = 1'b1;
  end

  assign bank_req   = (st_q == M_WR) | (st_q == M_RD);
  assign bank_we    = (st_q == M_WR);
  assign bank_addr  = addr_q;
  assign bank_wdata = wdata_q;
  assign bank_be    = strb_q;

  always_ff @(posedge bus.clk or negedge bus.rst_n) begin
    if (!bus.rst_n) begin
      st_q    <= M_IDLE;
      addr_q  <= '0;
      sel_q   <= '0;
      wdata_q <= '0;
      strb_q  <= '0;
      rdata_q <= '0;
    end else begin
      unique case (st_q)
        M_IDLE: begin
          if (wr_fire) begin
            addr_q  <= bus.awaddr[WORD_AW+1:2];
            sel_q   <= bus.awaddr[WORD_AW+BANK_W+1:WORD_AW+2];
            wdata_q <= bus.wdata;
            strb_q  <= bus.wstrb;
            st_q    <= M_WR;
          end else if (rd_fire) begin
            addr_q <= bus.araddr[WORD_AW+1:2];
            sel_q  <= bus.araddr[WORD_AW+BANK_W+1:WORD_AW+2];
            strb_q <= '1;
            st_q   <= M_RD;
          end
        end

        M_WR:    st_q <= M_BRESP;
        M_BRESP: if (bus.bready) st_q <= M_IDLE;

        M_RD:    st_q <= M_RWAIT;

        M_RWAIT: begin
          if (bank_rvalid[sel_q]) begin
            rdata_q <= bank_rdata[sel_q];
            st_q    <= M_RRESP;
          end
        end

        M_RRESP: if (bus.rready) st_q <= M_IDLE;

        default: st_q <= M_IDLE;
      endcase
    end
  end

endmodule : mem_ctrl


// ---------------------------------------------------------------------------
// mem_subsys -- PD_MEM.  Not header-switched: the arrays stay on VDD_MEM,
// which drops to the retention voltage in SLEEP_RET and DEEP_SLEEP.  The
// controller flops around them are the set_retention target.
// ---------------------------------------------------------------------------
module mem_subsys
  import aura_pkg::*;
#(
  parameter int unsigned N_BANK = aura_pkg::N_SRAM_BANK,
  parameter int unsigned WORDS  = aura_pkg::SRAM_DEPTH,
  parameter int unsigned AW     = aura_pkg::SRAM_AW
) (
  input  logic     clk,
  input  logic     rst_n,
  axi_lite_if.slv  bus,
  pmu_if.dom       pmu
);

  logic [N_BANK-1:0] bank_sel;
  logic              bank_req;
  logic              bank_we;
  logic [AW-1:0]     bank_addr;
  logic [DATA_W-1:0] bank_wdata;
  logic [STRB_W-1:0] bank_be;
  logic [DATA_W-1:0] bank_rdata [N_BANK];
  logic [N_BANK-1:0] bank_rvalid;

  logic              array_ret_n;
  logic              array_pd_n;

  // The arrays enter retention while the save strobe is active and stay there
  // until the restore strobe releases them; they are never powered down.
  logic ret_q;

  always_ff @(posedge pmu.pclk or negedge pmu.prst_n) begin
    if (!pmu.prst_n)          ret_q <= 1'b0;
    else if (pmu.ret_save)    ret_q <= 1'b1;
    else if (pmu.ret_restore) ret_q <= 1'b0;
  end

  assign array_ret_n = ~ret_q;
  assign array_pd_n  = 1'b1;

  mem_ctrl #(
    .N_BANK  (N_BANK),
    .WORD_AW (AW)
  ) u_ctrl (
    .bus         (bus),
    .bank_sel    (bank_sel),
    .bank_req    (bank_req),
    .bank_we     (bank_we),
    .bank_addr   (bank_addr),
    .bank_wdata  (bank_wdata),
    .bank_be     (bank_be),
    .bank_rdata  (bank_rdata),
    .bank_rvalid (bank_rvalid)
  );

  for (genvar b = 0; b < N_BANK; b++) begin : g_bank
    sram_bank #(
      .WORDS (WORDS),
      .AW    (AW)
    ) u_bank (
      .clk    (clk),
      .rst_n  (rst_n),
      .sel    (bank_sel[b]),
      .req    (bank_req),
      .we     (bank_we),
      .addr   (bank_addr),
      .wdata  (bank_wdata),
      .be     (bank_be),
      .rdata  (bank_rdata[b]),
      .rvalid (bank_rvalid[b]),
      .ret_n  (array_ret_n),
      .pd_n   (array_pd_n)
    );
  end

  // PD_MEM has no header switch, so the acknowledge simply tracks the enable.
  assign pmu.pwr_ack = pmu.pwr_en;

endmodule : mem_subsys
