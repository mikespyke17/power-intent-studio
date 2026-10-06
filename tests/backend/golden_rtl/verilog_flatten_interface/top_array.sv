module top_array (
    input logic clk,
    input logic rst_n
);
    bus_if arr_bus [2] (.clk(clk), .rst_n(rst_n));
    xbar u_xbar (.clk(clk), .rst_n(rst_n), .mst_port(arr_bus));
    leaf_mst u_mst0 (.clk(clk), .rst_n(rst_n), .b(arr_bus[0]));
endmodule
