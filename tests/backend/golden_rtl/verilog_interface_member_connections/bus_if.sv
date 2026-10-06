interface bus_if(input logic clk, input logic rst_n);
  logic req, we, rdata;
  modport mem (input clk, rst_n, req, we, output rdata);
endinterface
