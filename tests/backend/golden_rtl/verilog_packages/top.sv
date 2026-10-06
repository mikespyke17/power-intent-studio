module top (
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire [pkg::WIDTH-1:0] data_in,
    output wire [pkg::WIDTH-1:0] data_out
);
    sub u_sub (.clk(clk), .rst_n(rst_n), .d(data_in), .q(data_out));
endmodule
