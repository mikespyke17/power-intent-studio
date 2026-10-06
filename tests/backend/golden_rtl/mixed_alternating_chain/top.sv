module top (
    input  wire       clk,
    input  wire [7:0] data_in,
    output wire [7:0] data_out
);
    vhdl_middle u_mid (.clk(clk), .a(data_in), .y(data_out));
endmodule
