interface bus_if (input logic clk, input logic rst_n);
    logic data;
    modport mst (output data);
    modport slv (input data);
endinterface
