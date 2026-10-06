// ---------------------------------------------------------------------------
// axi_lite_mux2 -- merges two AXI4-Lite masters onto one.
//
// Port 1 is read-only (the cache refill port), so the write channels belong to
// port 0 unconditionally.  Reads alternate between the two ports.
// ---------------------------------------------------------------------------
module axi_lite_mux2
  import aura_pkg::*;
  import aura_axi_pkg::*;
(
  axi_lite_if.slv s0,   // data accesses, read and write
  axi_lite_if.slv s1,   // instruction refill, read only
  axi_lite_if.mst m
);

  // ---- write path: port 0 only --------------------------------------------
  assign m.awaddr   = s0.awaddr;
  assign m.awprot   = s0.awprot;
  assign m.awvalid  = s0.awvalid;
  assign s0.awready = m.awready;
  assign m.wdata    = s0.wdata;
  assign m.wstrb    = s0.wstrb;
  assign m.wvalid   = s0.wvalid;
  assign s0.wready  = m.wready;
  assign s0.bvalid  = m.bvalid;
  assign s0.bresp   = m.bresp;
  assign m.bready   = s0.bready;

  assign s1.awready = 1'b0;
  assign s1.wready  = 1'b0;
  assign s1.bvalid  = 1'b0;
  assign s1.bresp   = AXI_OKAY;

  // ---- read path: arbitrated ----------------------------------------------
  typedef enum logic [1:0] { A_IDLE, A_ADDR, A_DATA } ast_e;

  ast_e st_q;
  logic sel_q;     // 0 = port 0 granted, 1 = port 1 granted
  logic last_q;    // round-robin memory

  logic pick;
  always_comb begin
    if (s0.arvalid && s1.arvalid) pick = ~last_q;
    else if (s1.arvalid)          pick = 1'b1;
    else                          pick = 1'b0;
  end

  always_comb begin
    s0.arready = 1'b0;
    s1.arready = 1'b0;
    s0.rvalid  = 1'b0;
    s1.rvalid  = 1'b0;
    s0.rdata   = m.rdata;
    s1.rdata   = m.rdata;
    s0.rresp   = m.rresp;
    s1.rresp   = m.rresp;
    m.araddr   = sel_q ? s1.araddr : s0.araddr;
    m.arprot   = sel_q ? s1.arprot : s0.arprot;
    m.arvalid  = 1'b0;
    m.rready   = 1'b0;

    if (st_q == A_ADDR) begin
      m.arvalid = sel_q ? s1.arvalid : s0.arvalid;
      if (sel_q) s1.arready = m.arready;
      else       s0.arready = m.arready;
    end else if (st_q == A_DATA) begin
      m.rready = sel_q ? s1.rready : s0.rready;
      if (sel_q) s1.rvalid = m.rvalid;
      else       s0.rvalid = m.rvalid;
    end
  end

  always_ff @(posedge m.clk or negedge m.rst_n) begin
    if (!m.rst_n) begin
      st_q   <= A_IDLE;
      sel_q  <= 1'b0;
      last_q <= 1'b0;
    end else begin
      unique case (st_q)
        A_IDLE: begin
          if (s0.arvalid || s1.arvalid) begin
            sel_q  <= pick;
            last_q <= pick;
            st_q   <= A_ADDR;
          end
        end
        A_ADDR: if (m.arvalid && m.arready) st_q <= A_DATA;
        A_DATA: if (m.rvalid && m.rready)   st_q <= A_IDLE;
        default: st_q <= A_IDLE;
      endcase
    end
  end

endmodule : axi_lite_mux2


// ---------------------------------------------------------------------------
// pwr_ack_gen -- models the acknowledge that a real header-switch daisy chain
// returns once the switched rail has settled.  In the UPF this is the signal
// named by create_power_switch -ack_port.
// ---------------------------------------------------------------------------
module pwr_ack_gen #(
  parameter int unsigned DELAY = 6,
  parameter int unsigned CW    = (DELAY <= 2) ? 2 : $clog2(DELAY + 1)
) (
  input  logic clk,
  input  logic rst_n,
  input  logic pwr_en,
  output logic pwr_ack
);

  logic [CW-1:0] cnt_q;
  logic          ack_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt_q <= '0;
      ack_q <= 1'b0;
    end else if (!pwr_en) begin
      cnt_q <= '0;
      ack_q <= 1'b0;
    end else if (cnt_q == CW'(DELAY)) begin
      ack_q <= 1'b1;
    end else begin
      cnt_q <= cnt_q + CW'(1);
    end
  end

  assign pwr_ack = ack_q;

endmodule : pwr_ack_gen


// ---------------------------------------------------------------------------
// cpu_subsys -- PD_CPU.  Header-switched and retention-capable.
//
// The clock and reset arriving here are already gated and synchronised by the
// always-on domain, so this block only has to report its switch acknowledge
// and pass the retention / power-down controls down to the cache arrays.
// ---------------------------------------------------------------------------
module cpu_subsys
  import aura_pkg::*;
#(
  parameter logic [XLEN-1:0] RESET_PC = 32'h0000_0000
) (
  input  logic     clk,          // gated CPU clock
  input  logic     rst_n,        // synchronised CPU reset
  axi_lite_if.mst  mem_port,     // AXI4-Lite master into the fabric
  pmu_if.dom       pmu,

  output logic     illegal_instr,
  output logic     core_active
);

  axi_lite_if u_dbus (.clk(clk), .rst_n(rst_n));
  axi_lite_if u_ibus (.clk(clk), .rst_n(rst_n));

  logic            ifetch_req;
  logic [XLEN-1:0] ifetch_addr;
  logic [31:0]     ifetch_data;
  logic            ifetch_valid;
  logic            ifetch_err;

  logic            cache_ret_n;
  logic            cache_pd_n;

  // Retention save/restore keeps the arrays alive; the header switch takes the
  // logic supply away only after isolation has clamped.
  assign cache_ret_n = ~(pmu.ret_save | pmu.ret_restore);
  assign cache_pd_n  = pmu.pwr_en;

  rv_core #(.RESET_PC(RESET_PC)) u_core (
    .clk           (clk),
    .rst_n         (rst_n),
    .ifetch_req    (ifetch_req),
    .ifetch_addr   (ifetch_addr),
    .ifetch_data   (ifetch_data),
    .ifetch_valid  (ifetch_valid),
    .ifetch_err    (ifetch_err),
    .dbus          (u_dbus),
    .illegal_instr (illegal_instr),
    .core_active   (core_active)
  );

  icache #(
    .LINES (aura_pkg::IC_LINES)
  ) u_icache (
    .clk    (clk),
    .rst_n  (rst_n),
    .flush  (1'b0),
    .req    (ifetch_req),
    .addr   (ifetch_addr),
    .rdata  (ifetch_data),
    .rvalid (ifetch_valid),
    .rerr   (ifetch_err),
    .refill (u_ibus),
    .ret_n  (cache_ret_n),
    .pd_n   (cache_pd_n)
  );

  axi_lite_mux2 u_mux (
    .s0 (u_dbus),
    .s1 (u_ibus),
    .m  (mem_port)
  );

  pwr_ack_gen #(.DELAY(6)) u_ack (
    .clk     (pmu.pclk),
    .rst_n   (pmu.prst_n),
    .pwr_en  (pmu.pwr_en),
    .pwr_ack (pmu.pwr_ack)
  );

endmodule : cpu_subsys
