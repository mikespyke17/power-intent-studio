module mid (
    input  wire a,
    output wire y
);
    wire b;
    assign b = ~a;
    leaf u_leaf (.a(b), .y(y));
endmodule
