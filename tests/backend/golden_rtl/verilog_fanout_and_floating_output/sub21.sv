module sub21 (
    input wire  [31:0] i_q,
    output wire [31:0] o_q,
    input  wire        i_sleep,
    input  wire        i_iso,
    output  wire       o_sleep,
    output  wire       o_iso
);

    assign o_sleep = ~i_sleep;
    assign o_iso = i_iso;
    assign o_q = ~i_q;

endmodule
