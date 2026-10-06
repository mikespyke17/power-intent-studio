module top (
    input logic clk,
    input logic rst_n
);
    bus_if the_bus (.clk(clk), .rst_n(rst_n));
    leaf_mst u_mst (.clk(clk), .rst_n(rst_n), .b(the_bus));
    leaf_slv u_slv (.clk(clk), .rst_n(rst_n), .b(the_bus));
endmodule
