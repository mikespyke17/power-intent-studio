module sub (
    input  wire clk,
    input  wire d,
    output wire q
);
    assign q = d;
endmodule

module top (
    input  wire clk,
    input  wire d,
    output wire q
);
    sub u_sub (
        .clk(clk),
        .d  (d)
        .q  (q)
    );
endmodule
