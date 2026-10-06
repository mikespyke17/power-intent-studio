interface simple_bus;
    logic [7:0] data;
    logic       valid;
    modport producer (output data, output valid);
    modport consumer (input data, input valid);
endinterface

module consumer_mod (simple_bus.consumer bus);
    logic v;
    always @(*) v = bus.valid;
endmodule

module leaf;
endmodule

module top3;
    simple_bus buses [4] ();

    consumer_mod u_cons0 (.bus(buses[0]));
    consumer_mod u_cons2 (.bus(buses[2]));

    leaf u_leaves [3] ();
endmodule
