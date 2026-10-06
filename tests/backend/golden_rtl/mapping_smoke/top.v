module top (
    input  wire clk,
    input  wire sleep_n,
    output wire out1,
    output wire out2,
    output wire out3
);
    core u_core (.clk(clk), .out1(out1), .out2(out2), .out3(out3));
    dbg  u_dbg  (.clk(clk));
endmodule
