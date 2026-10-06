module top(input logic clk, input logic rst_n, input logic a, input logic b);
  bus_if bus(.clk(clk), .rst_n(rst_n));

  assign bus.req = a & b;

  outer u_outer(.bus(bus));
endmodule
