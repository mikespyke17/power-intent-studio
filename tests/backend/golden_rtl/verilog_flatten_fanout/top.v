module top (
    input wire a
);
    wire link;
    assign link = a;

    sink u_s0 (.a(link));
    sink u_s1 (.a(link));
    sink u_s2 (.a(link));
endmodule
