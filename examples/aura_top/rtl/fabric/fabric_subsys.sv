// ---------------------------------------------------------------------------
// axi2apb -- AXI4-Lite to APB4 bridge.  Writes take priority over reads.
// ---------------------------------------------------------------------------
module axi2apb
  import aura_pkg::*;
  import aura_axi_pkg::*;
#(
  parameter int unsigned APB_AW = 16
) (
  axi_lite_if.slv axi,
  apb_if.req      apb
);

  typedef enum logic [2:0] {
      B_IDLE, B_SETUP, B_ACCESS, B_BRESP, B_RRESP
  } bst_e;

  bst_e              st_q;
  logic              is_wr_q;
  logic [APB_AW-1:0] addr_q;
  logic [DATA_W-1:0] wdata_q;
  logic [STRB_W-1:0] strb_q;
  logic [DATA_W-1:0] rdata_q;
  logic              err_q;

  logic              wr_fire;
  logic              rd_fire;

  assign wr_fire = (st_q == B_IDLE) & axi.awvalid & axi.wvalid;
  assign rd_fire = (st_q == B_IDLE) & axi.arvalid & ~(axi.awvalid & axi.wvalid);

  assign axi.awready = wr_fire;
  assign axi.wready  = wr_fire;
  assign axi.arready = rd_fire;

  assign axi.bvalid  = (st_q == B_BRESP);
  assign axi.bresp   = err_q ? AXI_SLVERR : AXI_OKAY;
  assign axi.rvalid  = (st_q == B_RRESP);
  assign axi.rdata   = rdata_q;
  assign axi.rresp   = err_q ? AXI_SLVERR : AXI_OKAY;

  assign apb.paddr   = addr_q;
  assign apb.pprot   = 3'b000;
  assign apb.psel    = (st_q == B_SETUP) | (st_q == B_ACCESS);
  assign apb.penable = (st_q == B_ACCESS);
  assign apb.pwrite  = is_wr_q;
  assign apb.pwdata  = wdata_q;
  assign apb.pstrb   = is_wr_q ? strb_q : '0;

  always_ff @(posedge axi.clk or negedge axi.rst_n) begin
    if (!axi.rst_n) begin
      st_q    <= B_IDLE;
      is_wr_q <= 1'b0;
      addr_q  <= '0;
      wdata_q <= '0;
      strb_q  <= '0;
      rdata_q <= '0;
      err_q   <= 1'b0;
    end else begin
      unique case (st_q)
        B_IDLE: begin
          err_q <= 1'b0;
          if (wr_fire) begin
            is_wr_q <= 1'b1;
            addr_q  <= axi.awaddr[APB_AW-1:0];
            wdata_q <= axi.wdata;
            strb_q  <= axi.wstrb;
            st_q    <= B_SETUP;
          end else if (rd_fire) begin
            is_wr_q <= 1'b0;
            addr_q  <= axi.araddr[APB_AW-1:0];
            st_q    <= B_SETUP;
          end
        end

        B_SETUP: st_q <= B_ACCESS;

        B_ACCESS: begin
          if (apb.pready) begin
            rdata_q <= apb.prdata;
            err_q   <= apb.pslverr;
            st_q    <= is_wr_q ? B_BRESP : B_RRESP;
          end
        end

        B_BRESP: if (axi.bready) st_q <= B_IDLE;
        B_RRESP: if (axi.rready) st_q <= B_IDLE;

        default: st_q <= B_IDLE;
      endcase
    end
  end

endmodule : axi2apb


// ---------------------------------------------------------------------------
// fabric_subsys -- PD_TOP.  Always powered, so it is the safe place to put the
// isolation cells for every switchable initiator and target around it.
// ---------------------------------------------------------------------------
module fabric_subsys
  import aura_pkg::*;
#(
  parameter int unsigned N_M    = aura_pkg::N_AXI_MST,
  parameter int unsigned N_S    = aura_pkg::N_AXI_SLV,
  parameter int unsigned APB_AW = 16
) (
  input  logic clk,
  input  logic rst_n,
  axi_lite_if  mst_port [N_M],   // generic ports: the crossbar picks the
  axi_lite_if  slv_port [N_S],   // modport, and slot SLV_APB is re-used by
  apb_if.req   apb               // the bridge below
);

  axi_lite_xbar #(
    .N_M  (N_M),
    .N_S  (N_S),
    .BASE (aura_pkg::SLV_BASE),
    .MASK (aura_pkg::SLV_MASK)
  ) u_xbar (
    .clk      (clk),
    .rst_n    (rst_n),
    .mst_port (mst_port),
    .slv_port (slv_port)
  );

  axi2apb #(.APB_AW(APB_AW)) u_bridge (
    .axi (slv_port[aura_pkg::SLV_APB]),
    .apb (apb)
  );

endmodule : fabric_subsys
