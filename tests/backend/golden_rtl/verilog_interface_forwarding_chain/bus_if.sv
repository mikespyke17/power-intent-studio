interface bus_if(input logic clk, input logic rst_n);
  logic req;
  modport mem (input clk, rst_n, req);
endinterface
