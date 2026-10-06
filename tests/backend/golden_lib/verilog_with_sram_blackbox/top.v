module top (
    input  wire       clk,
    input  wire       we,
    input  wire [7:0] addr,
    inout  wire [7:0] data
);
    SRAM_256x8 u_sram (
        .clk(clk),
        .we(we),
        .addr(addr),
        .data(data)
    );
endmodule
