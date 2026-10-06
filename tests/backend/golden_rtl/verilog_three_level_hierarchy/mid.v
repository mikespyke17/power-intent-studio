module mid (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] d,
    output wire [7:0] q
);
    leaf u_leaf (.clk(clk), .rst_n(rst_n), .d(d), .q(q));
endmodule
