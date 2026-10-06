module top (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] data_in,
    output wire [7:0] data_out,
    output wire [7:0] vhdl_out
);
    known_leaf u_known (.clk(clk), .a(data_in), .y(data_out));

    vhdl_entity u_vhdl (
        .clk(clk),
        .rst_n(rst_n),
        .d(data_in),
        .q(vhdl_out)
    );
endmodule
