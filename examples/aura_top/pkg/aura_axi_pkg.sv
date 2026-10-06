// ---------------------------------------------------------------------------
// aura_axi_pkg -- AXI4-Lite channel payload types and helpers
// ---------------------------------------------------------------------------
package aura_axi_pkg;

  import aura_pkg::ADDR_W;
  import aura_pkg::DATA_W;
  import aura_pkg::STRB_W;

  typedef enum logic [1:0] {
      AXI_OKAY   = 2'b00,
      AXI_EXOKAY = 2'b01,
      AXI_SLVERR = 2'b10,
      AXI_DECERR = 2'b11
  } axi_resp_e;

  typedef struct packed {
      logic [ADDR_W-1:0] addr;
      logic [2:0]        prot;
  } axi_addr_t;

  typedef struct packed {
      logic [DATA_W-1:0] data;
      logic [STRB_W-1:0] strb;
  } axi_w_t;

  typedef struct packed {
      logic [DATA_W-1:0] data;
      axi_resp_e         resp;
  } axi_r_t;

  // Byte-lane merge used by every write-capable slave in the design.
  function automatic logic [DATA_W-1:0] axi_wr_merge(
      input logic [DATA_W-1:0] old_data,
      input logic [DATA_W-1:0] new_data,
      input logic [STRB_W-1:0] strb);
    logic [DATA_W-1:0] res;
    res = old_data;
    for (int unsigned b = 0; b < STRB_W; b++) begin
      if (strb[b]) res[8*b +: 8] = new_data[8*b +: 8];
    end
    return res;
  endfunction

endpackage : aura_axi_pkg
