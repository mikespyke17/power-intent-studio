// ---------------------------------------------------------------------------
// axi_lite_if -- AXI4-Lite channel bundle
// ---------------------------------------------------------------------------
interface axi_lite_if
  import aura_pkg::*;
  import aura_axi_pkg::*;
#(
  parameter int unsigned AW = aura_pkg::ADDR_W,
  parameter int unsigned DW = aura_pkg::DATA_W
) (
  input logic clk,
  input logic rst_n
);

  localparam int unsigned SW = DW / 8;

  // Write address
  logic [AW-1:0] awaddr;
  logic [2:0]    awprot;
  logic          awvalid;
  logic          awready;

  // Write data
  logic [DW-1:0] wdata;
  logic [SW-1:0] wstrb;
  logic          wvalid;
  logic          wready;

  // Write response
  axi_resp_e     bresp;
  logic          bvalid;
  logic          bready;

  // Read address
  logic [AW-1:0] araddr;
  logic [2:0]    arprot;
  logic          arvalid;
  logic          arready;

  // Read data
  logic [DW-1:0] rdata;
  axi_resp_e     rresp;
  logic          rvalid;
  logic          rready;

  modport mst (
    input  clk, rst_n,
    output awaddr, awprot, awvalid,
    input  awready,
    output wdata, wstrb, wvalid,
    input  wready,
    input  bresp, bvalid,
    output bready,
    output araddr, arprot, arvalid,
    input  arready,
    input  rdata, rresp, rvalid,
    output rready
  );

  modport slv (
    input  clk, rst_n,
    input  awaddr, awprot, awvalid,
    output awready,
    input  wdata, wstrb, wvalid,
    output wready,
    output bresp, bvalid,
    input  bready,
    input  araddr, arprot, arvalid,
    output arready,
    output rdata, rresp, rvalid,
    input  rready
  );

  modport mon (
    input clk, rst_n,
    input awaddr, awprot, awvalid, awready,
    input wdata, wstrb, wvalid, wready,
    input bresp, bvalid, bready,
    input araddr, arprot, arvalid, arready,
    input rdata, rresp, rvalid, rready
  );

endinterface : axi_lite_if
