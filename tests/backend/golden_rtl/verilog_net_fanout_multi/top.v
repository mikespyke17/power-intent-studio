module top;
    wire [7:0] link;
    driver   u_drv  (.out(link));
    receiver u_rcv0 (.in(link));
    receiver u_rcv1 (.in(link));
    receiver u_rcv2 (.in(link));
endmodule
