module top;
    wire [7:0] link;
    driver   u_drv (.out(link));
    receiver u_rcv (.in(link));
endmodule
