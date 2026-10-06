module top (
    input  wire        clk,
    input  wire         rst_n,
    input  wire [7:0]   data_in_a,
    output wire [7:0]   data_out_a,
    input  wire [15:0]  data_in_b,
    output wire [15:0]  data_out_b
);
    sub #(.WIDTH(8))  u_sub_a (.clk(clk), .rst_n(rst_n), .d(data_in_a), .q(data_out_a));
    sub #(.WIDTH(16)) u_sub_b (.clk(clk), .rst_n(rst_n), .d(data_in_b), .q(data_out_b));
endmodule
