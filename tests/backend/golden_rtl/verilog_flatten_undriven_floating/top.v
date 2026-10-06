module top (
    input  wire a,
    output wire y
);
    wire undriven_sig;
    wire unused_sig;

    assign y = undriven_sig;
    assign unused_sig = a;
endmodule
