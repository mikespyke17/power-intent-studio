// ---------------------------------------------------------------------------
// sram_1rw_wrap -- adapts the active-high sram_if protocol to the active-low
//                  hard-macro pinout and generates the rvalid pulse.
//
// The generate block below selects the compiled macro configuration.  Adding a
// new depth means compiling a new macro and adding a branch here; there is
// deliberately no default, so an unsupported size is an elaboration error
// rather than a silently missing memory.
// ---------------------------------------------------------------------------
module sram_1rw_wrap #(
  parameter int unsigned WORDS = aura_pkg::SRAM_DEPTH,
  parameter int unsigned BITS  = aura_pkg::DATA_W,
  parameter int unsigned AW    = $clog2(WORDS),
  parameter int unsigned BW    = BITS / 8
) (
  sram_if.mem      bus,
  input  logic     ret_n,   // 0 = macro array in retention
  input  logic     pd_n     // 0 = macro powered down
);

  logic [BITS-1:0] q_macro;
  logic            rd_pending;

  if ((WORDS == 1024) && (BITS == 32)) begin : g_1024x32
    sram_1rw_1024x32 u_macro (
      .CLK  (bus.clk),
      .CEN  (~bus.req),
      .WEN  (~bus.we),
      .BEN  (~bus.be),
      .A    (bus.addr[9:0]),
      .D    (bus.wdata),
      .Q    (q_macro),
      .RETN (ret_n),
      .PDN  (pd_n)
    );
  end else if ((WORDS == 128) && (BITS == 32)) begin : g_128x32
    sram_1rw_128x32 u_macro (
      .CLK  (bus.clk),
      .CEN  (~bus.req),
      .WEN  (~bus.we),
      .BEN  (~bus.be),
      .A    (bus.addr[6:0]),
      .D    (bus.wdata),
      .Q    (q_macro),
      .RETN (ret_n),
      .PDN  (pd_n)
    );
  end else begin : g_unsupported
    $fatal(1, "sram_1rw_wrap: no compiled macro for %0dx%0d", WORDS, BITS);
  end

  always_ff @(posedge bus.clk or negedge bus.rst_n) begin
    if (!bus.rst_n) rd_pending <= 1'b0;
    else            rd_pending <= bus.req & ~bus.we & ret_n & pd_n;
  end

  assign bus.rdata  = q_macro;
  assign bus.rvalid = rd_pending;

endmodule : sram_1rw_wrap


// ---------------------------------------------------------------------------
// sram_2rw_wrap -- dual-port wrapper.  Port A follows sram_if, port B is a
//                  raw streaming read port for the DSP sequencer.
// ---------------------------------------------------------------------------
module sram_2rw_wrap #(
  parameter int unsigned WORDS = aura_dsp_pkg::DSP_MEM_DEPTH,
  parameter int unsigned BITS  = aura_pkg::DATA_W,
  parameter int unsigned AW    = $clog2(WORDS),
  parameter int unsigned BW    = BITS / 8
) (
  sram_if.mem              bus_a,
  input  logic             b_req,
  input  logic [AW-1:0]    b_addr,
  output logic [BITS-1:0]  b_rdata,
  output logic             b_rvalid,
  input  logic             ret_n,
  input  logic             pd_n
);

  logic [BITS-1:0] qa;
  logic            a_rd_pending;
  logic            b_rd_pending;

  if ((WORDS == 512) && (BITS == 32)) begin : g_512x32
    sram_2rw_512x32 u_macro (
      .CLKA (bus_a.clk),
      .CENA (~bus_a.req),
      .WENA (~bus_a.we),
      .BENA (~bus_a.be),
      .AA   (bus_a.addr[8:0]),
      .DA   (bus_a.wdata),
      .QA   (qa),
      .CLKB (bus_a.clk),
      .CENB (~b_req),
      .AB   (b_addr),
      .QB   (b_rdata),
      .RETN (ret_n),
      .PDN  (pd_n)
    );
  end else begin : g_unsupported
    $fatal(1, "sram_2rw_wrap: no compiled macro for %0dx%0d", WORDS, BITS);
  end

  always_ff @(posedge bus_a.clk or negedge bus_a.rst_n) begin
    if (!bus_a.rst_n) begin
      a_rd_pending <= 1'b0;
      b_rd_pending <= 1'b0;
    end else begin
      a_rd_pending <= bus_a.req & ~bus_a.we & ret_n & pd_n;
      b_rd_pending <= b_req & ret_n & pd_n;
    end
  end

  assign bus_a.rdata  = qa;
  assign bus_a.rvalid = a_rd_pending;
  assign b_rvalid     = b_rd_pending;

endmodule : sram_2rw_wrap
