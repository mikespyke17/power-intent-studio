// ---------------------------------------------------------------------------
// sram_2rw_512x32 -- dual-port (1 write / 1 read) synchronous SRAM hard macro.
//
// Used by the DSP sample memory, where the sequencer streams samples out of
// port B while the fabric writes new ones through port A.  Cell name matches
// lib/aura_sram_*.lib.
// ---------------------------------------------------------------------------
(* dont_touch = "true" *)
(* black_box_pad_pin = "VDD,VSS" *)
module sram_2rw_512x32 (
  input  logic        CLKA,
  input  logic        CENA,   // port A chip enable, active low
  input  logic        WENA,   // port A write enable, active low
  input  logic [3:0]  BENA,
  input  logic [8:0]  AA,
  input  logic [31:0] DA,
  output logic [31:0] QA,

  input  logic        CLKB,
  input  logic        CENB,   // port B chip enable, active low
  input  logic [8:0]  AB,
  output logic [31:0] QB,

  input  logic        RETN,
  input  logic        PDN
);

// synopsys translate_off
`ifndef SYNTHESIS
  logic [31:0] mem [512];
  logic [31:0] qa_r;
  logic [31:0] qb_r;

  always_ff @(posedge CLKA) begin
    if (!PDN) begin
      qa_r <= 'x;
    end else if (RETN && !CENA) begin
      if (!WENA) begin
        for (int unsigned b = 0; b < 4; b++) begin
          if (!BENA[b]) mem[AA][8*b +: 8] <= DA[8*b +: 8];
        end
        qa_r <= 'x;
      end else begin
        qa_r <= mem[AA];
      end
    end
  end

  always_ff @(posedge CLKB) begin
    if (!PDN)                    qb_r <= 'x;
    else if (RETN && !CENB)      qb_r <= mem[AB];
  end

  assign QA = qa_r;
  assign QB = qb_r;
`endif
// synopsys translate_on

endmodule : sram_2rw_512x32
