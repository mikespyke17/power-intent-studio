// ---------------------------------------------------------------------------
// apb_if -- APB4 requester/completer bundle
// ---------------------------------------------------------------------------
interface apb_if #(
  parameter int unsigned AW = 16,
  parameter int unsigned DW = aura_pkg::DATA_W
) (
  input logic clk,
  input logic rst_n
);

  localparam int unsigned SW = DW / 8;

  logic [AW-1:0] paddr;
  logic [2:0]    pprot;
  logic          psel;
  logic          penable;
  logic          pwrite;
  logic [DW-1:0] pwdata;
  logic [SW-1:0] pstrb;
  logic [DW-1:0] prdata;
  logic          pready;
  logic          pslverr;

  modport req (
    input  clk, rst_n,
    output paddr, pprot, psel, penable, pwrite, pwdata, pstrb,
    input  prdata, pready, pslverr
  );

  modport cmp (
    input  clk, rst_n,
    input  paddr, pprot, psel, penable, pwrite, pwdata, pstrb,
    output prdata, pready, pslverr
  );

endinterface : apb_if
