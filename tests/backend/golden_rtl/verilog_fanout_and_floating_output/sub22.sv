module sub22 (
    input  wire        i_sleep,
    output  wire       o_sleep
);

    assign o_sleep = ~i_sleep;

endmodule
