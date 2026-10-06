module mid_wrap(bus_if.mem bus);
  logic q_int;
  leaf_macro u_leaf(.clk(bus.clk), .cen(~bus.req), .wen(~bus.we), .q(q_int));
  assign bus.rdata = q_int;
endmodule
