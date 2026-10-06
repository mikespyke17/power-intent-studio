module top (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] data_in,
    output wire [7:0] data_out
);
    mid u_mid (.clk(clk), .rst_n(rst_n), .d(data_in), .q(data_out));
endmodule
