module sub (
    input  wire [31:0] i_q,
    output wire [31:0] o_q
);

    assign o_q = ~i_q;

endmodule
