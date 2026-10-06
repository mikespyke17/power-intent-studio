// A real, simulatable RTL body for the exact same cell the accompanying
// sram.lib describes -- the common real pairing (memory-compiler macro
// shipped with both a behavioral model for simulation and a .lib for
// synthesis/timing) that LIB priority exists for. `dummy_marker` is here
// purely so a test can tell "the real RTL body was used" apart from "the
// .lib black box was used" (the black box has no nets at all).
module SRAM_256x8 (
    input  wire       clk,
    input  wire       we,
    input  wire [7:0] addr,
    inout  wire [7:0] data
);
    wire dummy_marker;
    assign dummy_marker = clk;
endmodule
