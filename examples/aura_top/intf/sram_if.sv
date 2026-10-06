// ---------------------------------------------------------------------------
// sram_if -- controller <-> memory macro wrapper handshake
// ---------------------------------------------------------------------------
interface sram_if #(
  parameter int unsigned AW = aura_pkg::SRAM_AW,
  parameter int unsigned DW = aura_pkg::DATA_W
) (
  input logic clk,
  input logic rst_n
);

  localparam int unsigned SW = DW / 8;

  logic          req;      // access request, active high
  logic          we;       // 1 = write, 0 = read
  logic [AW-1:0] addr;
  logic [DW-1:0] wdata;
  logic [SW-1:0] be;       // byte enables
  logic [DW-1:0] rdata;
  logic          rvalid;   // rdata valid, one cycle after a read request

  modport ctrl (
    input  clk, rst_n,
    output req, we, addr, wdata, be,
    input  rdata, rvalid
  );

  modport mem (
    input  clk, rst_n,
    input  req, we, addr, wdata, be,
    output rdata, rvalid
  );

endinterface : sram_if
