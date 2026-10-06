// ---------------------------------------------------------------------------
// axi_lite_dec -- address decoder shared by the crossbar instances.
// ---------------------------------------------------------------------------
module axi_lite_dec
  import aura_pkg::*;
#(
  parameter int unsigned       N_S    = aura_pkg::N_AXI_SLV,
  parameter logic [ADDR_W-1:0] BASE [N_S] = aura_pkg::SLV_BASE,
  parameter logic [ADDR_W-1:0] MASK [N_S] = aura_pkg::SLV_MASK,
  parameter int unsigned       SIDX_W = (N_S <= 1) ? 1 : $clog2(N_S)
) (
  input  logic [ADDR_W-1:0] addr,
  output logic [SIDX_W-1:0] sel,
  output logic              hit
);

  always_comb begin
    sel = '0;
    hit = 1'b0;
    for (int unsigned s = 0; s < N_S; s++) begin
      if (!hit && ((addr & MASK[s]) == BASE[s])) begin
        sel = SIDX_W'(s);
        hit = 1'b1;
      end
    end
  end

endmodule : axi_lite_dec


// ---------------------------------------------------------------------------
// axi_lite_rr -- round-robin request picker.
// ---------------------------------------------------------------------------
module axi_lite_rr #(
  parameter int unsigned N      = 2,
  parameter int unsigned IDX_W  = (N <= 1) ? 1 : $clog2(N)
) (
  input  logic [N-1:0]     req,
  input  logic [IDX_W-1:0] ptr,
  output logic [IDX_W-1:0] gnt_idx,
  output logic             gnt_valid
);

  always_comb begin
    gnt_idx   = '0;
    gnt_valid = 1'b0;
    for (int unsigned i = 0; i < N; i++) begin
      int unsigned j;
      j = (int'(ptr) + i) % N;
      if (!gnt_valid && req[j]) begin
        gnt_idx   = IDX_W'(j);
        gnt_valid = 1'b1;
      end
    end
  end

endmodule : axi_lite_rr


// ---------------------------------------------------------------------------
// axi_lite_xbar -- transaction-serialising AXI4-Lite interconnect.
//
// One read and one write transaction is in flight at a time; masters are
// picked round-robin.  Unmapped addresses are answered with DECERR by the
// crossbar itself so a stray access cannot hang the fabric.
// ---------------------------------------------------------------------------
module axi_lite_xbar
  import aura_pkg::*;
  import aura_axi_pkg::*;
#(
  parameter int unsigned       N_M    = aura_pkg::N_AXI_MST,
  parameter int unsigned       N_S    = aura_pkg::N_AXI_SLV,
  parameter logic [ADDR_W-1:0] BASE [N_S] = aura_pkg::SLV_BASE,
  parameter logic [ADDR_W-1:0] MASK [N_S] = aura_pkg::SLV_MASK,
  parameter int unsigned       MIDX_W = (N_M <= 1) ? 1 : $clog2(N_M),
  parameter int unsigned       SIDX_W = (N_S <= 1) ? 1 : $clog2(N_S)
) (
  input  logic    clk,
  input  logic    rst_n,
  axi_lite_if.slv mst_port [N_M],   // crossbar is the slave to the masters
  axi_lite_if.mst slv_port [N_S]    // crossbar is the master to the slaves
);

  // ---- flatten the master-side interface array ----------------------------
  logic [N_M-1:0][ADDR_W-1:0] m_awaddr;
  logic [N_M-1:0]             m_awvalid, m_awready;
  logic [N_M-1:0][DATA_W-1:0] m_wdata;
  logic [N_M-1:0][STRB_W-1:0] m_wstrb;
  logic [N_M-1:0]             m_wvalid,  m_wready;
  logic [N_M-1:0]             m_bvalid,  m_bready;
  axi_resp_e                  m_bresp   [N_M];
  logic [N_M-1:0][ADDR_W-1:0] m_araddr;
  logic [N_M-1:0]             m_arvalid, m_arready;
  logic [N_M-1:0][DATA_W-1:0] m_rdata;
  logic [N_M-1:0]             m_rvalid,  m_rready;
  axi_resp_e                  m_rresp   [N_M];

  for (genvar m = 0; m < N_M; m++) begin : g_mflat
    assign m_awaddr[m]         = mst_port[m].awaddr;
    assign m_awvalid[m]        = mst_port[m].awvalid;
    assign mst_port[m].awready = m_awready[m];
    assign m_wdata[m]          = mst_port[m].wdata;
    assign m_wstrb[m]          = mst_port[m].wstrb;
    assign m_wvalid[m]         = mst_port[m].wvalid;
    assign mst_port[m].wready  = m_wready[m];
    assign mst_port[m].bvalid  = m_bvalid[m];
    assign mst_port[m].bresp   = m_bresp[m];
    assign m_bready[m]         = mst_port[m].bready;
    assign m_araddr[m]         = mst_port[m].araddr;
    assign m_arvalid[m]        = mst_port[m].arvalid;
    assign mst_port[m].arready = m_arready[m];
    assign mst_port[m].rdata   = m_rdata[m];
    assign mst_port[m].rresp   = m_rresp[m];
    assign mst_port[m].rvalid  = m_rvalid[m];
    assign m_rready[m]         = mst_port[m].rready;
  end

  // ---- flatten the slave-side interface array -----------------------------
  logic [N_S-1:0][ADDR_W-1:0] s_awaddr;
  logic [N_S-1:0]             s_awvalid, s_awready;
  logic [N_S-1:0][DATA_W-1:0] s_wdata;
  logic [N_S-1:0][STRB_W-1:0] s_wstrb;
  logic [N_S-1:0]             s_wvalid,  s_wready;
  logic [N_S-1:0]             s_bvalid,  s_bready;
  axi_resp_e                  s_bresp   [N_S];
  logic [N_S-1:0][ADDR_W-1:0] s_araddr;
  logic [N_S-1:0]             s_arvalid, s_arready;
  logic [N_S-1:0][DATA_W-1:0] s_rdata;
  logic [N_S-1:0]             s_rvalid,  s_rready;
  axi_resp_e                  s_rresp   [N_S];

  for (genvar s = 0; s < N_S; s++) begin : g_sflat
    assign slv_port[s].awaddr  = s_awaddr[s];
    assign slv_port[s].awprot  = 3'b000;
    assign slv_port[s].awvalid = s_awvalid[s];
    assign s_awready[s]        = slv_port[s].awready;
    assign slv_port[s].wdata   = s_wdata[s];
    assign slv_port[s].wstrb   = s_wstrb[s];
    assign slv_port[s].wvalid  = s_wvalid[s];
    assign s_wready[s]         = slv_port[s].wready;
    assign s_bvalid[s]         = slv_port[s].bvalid;
    assign s_bresp[s]          = slv_port[s].bresp;
    assign slv_port[s].bready  = s_bready[s];
    assign slv_port[s].araddr  = s_araddr[s];
    assign slv_port[s].arprot  = 3'b000;
    assign slv_port[s].arvalid = s_arvalid[s];
    assign s_arready[s]        = slv_port[s].arready;
    assign s_rdata[s]          = slv_port[s].rdata;
    assign s_rresp[s]          = slv_port[s].rresp;
    assign s_rvalid[s]         = slv_port[s].rvalid;
    assign slv_port[s].rready  = s_rready[s];
  end

  // ---- write channel state -------------------------------------------------
  typedef enum logic [1:0] { W_IDLE, W_XFER, W_RESP, W_DECERR } wstate_e;

  wstate_e            w_state_q;
  logic [MIDX_W-1:0]  w_mst_q;
  logic [SIDX_W-1:0]  w_slv_q;
  logic               w_aw_done_q;
  logic               w_w_done_q;
  logic [MIDX_W-1:0]  w_ptr_q;

  logic [N_M-1:0]     w_req;
  logic [MIDX_W-1:0]  w_gnt_idx;
  logic               w_gnt_val;
  logic [SIDX_W-1:0]  w_dec_sel;
  logic               w_dec_hit;

  for (genvar m = 0; m < N_M; m++) begin : g_wreq
    assign w_req[m] = m_awvalid[m] & m_wvalid[m];
  end

  axi_lite_rr #(.N(N_M), .IDX_W(MIDX_W)) u_w_arb (
    .req       (w_req),
    .ptr       (w_ptr_q),
    .gnt_idx   (w_gnt_idx),
    .gnt_valid (w_gnt_val)
  );

  axi_lite_dec #(
    .N_S(N_S), .BASE(BASE), .MASK(MASK), .SIDX_W(SIDX_W)
  ) u_w_dec (
    .addr (m_awaddr[w_gnt_idx]),
    .sel  (w_dec_sel),
    .hit  (w_dec_hit)
  );

  // ---- read channel state --------------------------------------------------
  typedef enum logic [1:0] { R_IDLE, R_ADDR, R_DATA, R_DECERR } rstate_e;

  rstate_e            r_state_q;
  logic [MIDX_W-1:0]  r_mst_q;
  logic [SIDX_W-1:0]  r_slv_q;
  logic [MIDX_W-1:0]  r_ptr_q;

  logic [N_M-1:0]     r_req;
  logic [MIDX_W-1:0]  r_gnt_idx;
  logic               r_gnt_val;
  logic [SIDX_W-1:0]  r_dec_sel;
  logic               r_dec_hit;

  assign r_req = m_arvalid;

  axi_lite_rr #(.N(N_M), .IDX_W(MIDX_W)) u_r_arb (
    .req       (r_req),
    .ptr       (r_ptr_q),
    .gnt_idx   (r_gnt_idx),
    .gnt_valid (r_gnt_val)
  );

  axi_lite_dec #(
    .N_S(N_S), .BASE(BASE), .MASK(MASK), .SIDX_W(SIDX_W)
  ) u_r_dec (
    .addr (m_araddr[r_gnt_idx]),
    .sel  (r_dec_sel),
    .hit  (r_dec_hit)
  );

  // ---- combinational routing ----------------------------------------------
  always_comb begin
    m_awready = '0;
    m_wready  = '0;
    m_bvalid  = '0;
    m_arready = '0;
    m_rvalid  = '0;
    s_awvalid = '0;
    s_wvalid  = '0;
    s_bready  = '0;
    s_arvalid = '0;
    s_rready  = '0;
    s_awaddr  = '0;
    s_wdata   = '0;
    s_wstrb   = '0;
    s_araddr  = '0;

    for (int unsigned m = 0; m < N_M; m++) begin
      m_bresp[m] = AXI_OKAY;
      m_rresp[m] = AXI_OKAY;
      m_rdata[m] = '0;
    end

    // write path
    if (w_state_q == W_XFER) begin
      s_awaddr[w_slv_q]  = m_awaddr[w_mst_q];
      s_wdata[w_slv_q]   = m_wdata[w_mst_q];
      s_wstrb[w_slv_q]   = m_wstrb[w_mst_q];
      s_awvalid[w_slv_q] = m_awvalid[w_mst_q] & ~w_aw_done_q;
      s_wvalid[w_slv_q]  = m_wvalid[w_mst_q]  & ~w_w_done_q;
      m_awready[w_mst_q] = s_awready[w_slv_q] & ~w_aw_done_q;
      m_wready[w_mst_q]  = s_wready[w_slv_q]  & ~w_w_done_q;
    end else if (w_state_q == W_RESP) begin
      m_bvalid[w_mst_q]  = s_bvalid[w_slv_q];
      m_bresp[w_mst_q]   = s_bresp[w_slv_q];
      s_bready[w_slv_q]  = m_bready[w_mst_q];
    end else if (w_state_q == W_DECERR) begin
      m_bvalid[w_mst_q]  = 1'b1;
      m_bresp[w_mst_q]   = AXI_DECERR;
      m_awready[w_mst_q] = 1'b0;
    end

    // an unmapped write still has to consume its address and data beats
    if ((w_state_q == W_IDLE) && w_gnt_val && !w_dec_hit) begin
      m_awready[w_gnt_idx] = 1'b1;
      m_wready[w_gnt_idx]  = 1'b1;
    end

    // read path
    if (r_state_q == R_ADDR) begin
      s_araddr[r_slv_q]  = m_araddr[r_mst_q];
      s_arvalid[r_slv_q] = m_arvalid[r_mst_q];
      m_arready[r_mst_q] = s_arready[r_slv_q];
    end else if (r_state_q == R_DATA) begin
      m_rvalid[r_mst_q]  = s_rvalid[r_slv_q];
      m_rdata[r_mst_q]   = s_rdata[r_slv_q];
      m_rresp[r_mst_q]   = s_rresp[r_slv_q];
      s_rready[r_slv_q]  = m_rready[r_mst_q];
    end else if (r_state_q == R_DECERR) begin
      m_rvalid[r_mst_q]  = 1'b1;
      m_rdata[r_mst_q]   = '0;
      m_rresp[r_mst_q]   = AXI_DECERR;
    end

    if ((r_state_q == R_IDLE) && r_gnt_val && !r_dec_hit) begin
      m_arready[r_gnt_idx] = 1'b1;
    end
  end

  // ---- sequential control --------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      w_state_q   <= W_IDLE;
      w_mst_q     <= '0;
      w_slv_q     <= '0;
      w_aw_done_q <= 1'b0;
      w_w_done_q  <= 1'b0;
      w_ptr_q     <= '0;
    end else begin
      unique case (w_state_q)
        W_IDLE: begin
          if (w_gnt_val) begin
            w_mst_q     <= w_gnt_idx;
            w_ptr_q     <= (w_gnt_idx == MIDX_W'(N_M - 1)) ? '0
                                                           : (w_gnt_idx + MIDX_W'(1));
            w_aw_done_q <= 1'b0;
            w_w_done_q  <= 1'b0;
            if (w_dec_hit) begin
              w_slv_q   <= w_dec_sel;
              w_state_q <= W_XFER;
            end else begin
              w_state_q <= W_DECERR;
            end
          end
        end

        W_XFER: begin
          if (s_awvalid[w_slv_q] && s_awready[w_slv_q]) w_aw_done_q <= 1'b1;
          if (s_wvalid[w_slv_q]  && s_wready[w_slv_q])  w_w_done_q  <= 1'b1;
          if ((w_aw_done_q || (s_awvalid[w_slv_q] && s_awready[w_slv_q])) &&
              (w_w_done_q  || (s_wvalid[w_slv_q]  && s_wready[w_slv_q]))) begin
            w_state_q <= W_RESP;
          end
        end

        W_RESP: begin
          if (s_bvalid[w_slv_q] && m_bready[w_mst_q]) w_state_q <= W_IDLE;
        end

        W_DECERR: begin
          if (m_bready[w_mst_q]) w_state_q <= W_IDLE;
        end

        default: w_state_q <= W_IDLE;
      endcase
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      r_state_q <= R_IDLE;
      r_mst_q   <= '0;
      r_slv_q   <= '0;
      r_ptr_q   <= '0;
    end else begin
      unique case (r_state_q)
        R_IDLE: begin
          if (r_gnt_val) begin
            r_mst_q <= r_gnt_idx;
            r_ptr_q <= (r_gnt_idx == MIDX_W'(N_M - 1)) ? '0
                                                       : (r_gnt_idx + MIDX_W'(1));
            if (r_dec_hit) begin
              r_slv_q   <= r_dec_sel;
              r_state_q <= R_ADDR;
            end else begin
              r_state_q <= R_DECERR;
            end
          end
        end

        R_ADDR: begin
          if (s_arvalid[r_slv_q] && s_arready[r_slv_q]) r_state_q <= R_DATA;
        end

        R_DATA: begin
          if (s_rvalid[r_slv_q] && m_rready[r_mst_q]) r_state_q <= R_IDLE;
        end

        R_DECERR: begin
          if (m_rready[r_mst_q]) r_state_q <= R_IDLE;
        end

        default: r_state_q <= R_IDLE;
      endcase
    end
  end

endmodule : axi_lite_xbar
