module sub(
    input a,
    input b,
    input sel,
    input [3:0] narrow,
    output y_passthrough,
    output y_inverted,
    output y_mux,
    output y_and,
    output [7:0] y_widened
);
    assign y_passthrough = a;
    assign y_inverted = ~a;
    assign y_mux = sel ? a : b;
    assign y_and = a & b;
    assign y_widened = narrow;
endmodule
