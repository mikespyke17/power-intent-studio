module top_bank(
  input  logic clk,
  input  logic rst_n,
  input  logic sel,
  input  logic req_in,
  input  logic we_in,
  output logic rdata_out
);
  bus_if bus(.clk(clk), .rst_n(rst_n));

  assign bus.req    = sel & req_in;
  assign bus.we     = we_in;
  assign rdata_out  = bus.rdata;

  mid_wrap u_wrap(.bus(bus));
endmodule
