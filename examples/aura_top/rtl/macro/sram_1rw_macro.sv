// ---------------------------------------------------------------------------
// Single-port synchronous SRAM hard macros.
//
// One module per compiled configuration, exactly as a memory compiler emits
// them -- a parameterised module could not be given a Liberty model, because
// the bus widths differ between configurations.  Cell names match
// lib/aura_sram_*.lib so a Liberty-to-RTL binding pass resolves them directly.
//
// Foundry-style pin naming, active-low controls.  Synthesis treats these as
// black boxes; the behavioural bodies exist for simulation only.  In UPF their
// power pins are connected explicitly with connect_supply_net.
// ---------------------------------------------------------------------------

(* dont_touch = "true" *)
(* black_box_pad_pin = "VDD,VSS" *)
module sram_1rw_1024x32 (
  input  logic        CLK,
  input  logic        CEN,   // chip enable, active low
  input  logic        WEN,   // write enable, active low
  input  logic [3:0]  BEN,   // byte write enable, active low
  input  logic [9:0]  A,
  input  logic [31:0] D,
  output logic [31:0] Q,
  input  logic        RETN,  // retention mode, active low
  input  logic        PDN    // power down, active low
);

// synopsys translate_off
`ifndef SYNTHESIS
  logic [31:0] mem [1024];
  logic [31:0] q_r;

  always_ff @(posedge CLK) begin
    if (!PDN) begin
      q_r <= 'x;
    end else if (RETN && !CEN) begin
      if (!WEN) begin
        for (int unsigned b = 0; b < 4; b++) begin
          if (!BEN[b]) mem[A][8*b +: 8] <= D[8*b +: 8];
        end
        q_r <= 'x;                       // no write-through in this macro
      end else begin
        q_r <= mem[A];
      end
    end
  end

  assign Q = q_r;

  always_ff @(posedge CLK) begin
    if (PDN && RETN && !CEN && (^A === 1'bx))
      $error("%m: unknown address while chip enabled");
  end
`endif
// synopsys translate_on

endmodule : sram_1rw_1024x32


(* dont_touch = "true" *)
(* black_box_pad_pin = "VDD,VSS" *)
module sram_1rw_128x32 (
  input  logic        CLK,
  input  logic        CEN,
  input  logic        WEN,
  input  logic [3:0]  BEN,
  input  logic [6:0]  A,
  input  logic [31:0] D,
  output logic [31:0] Q,
  input  logic        RETN,
  input  logic        PDN
);

// synopsys translate_off
`ifndef SYNTHESIS
  logic [31:0] mem [128];
  logic [31:0] q_r;

  always_ff @(posedge CLK) begin
    if (!PDN) begin
      q_r <= 'x;
    end else if (RETN && !CEN) begin
      if (!WEN) begin
        for (int unsigned b = 0; b < 4; b++) begin
          if (!BEN[b]) mem[A][8*b +: 8] <= D[8*b +: 8];
        end
        q_r <= 'x;
      end else begin
        q_r <= mem[A];
      end
    end
  end

  assign Q = q_r;

  always_ff @(posedge CLK) begin
    if (PDN && RETN && !CEN && (^A === 1'bx))
      $error("%m: unknown address while chip enabled");
  end
`endif
// synopsys translate_on

endmodule : sram_1rw_128x32
