interface simple_bus;
    logic [7:0] data;
    logic       valid;
    modport producer (output data, output valid);
    modport consumer (input data, input valid);
endinterface

module producer_mod (simple_bus.producer bus);
    always @(*) begin
        bus.data = 8'hAA;
        bus.valid = 1'b1;
    end
endmodule

module consumer_mod (simple_bus.consumer bus);
    logic v;
    always @(*) v = bus.valid;
endmodule

// Real, common idiom: this module's own interface port is left
// unconstrained but is immediately forwarded, unmodified, to a single
// modport-qualified child -- the effective modport should be inferable.
module fwd_single (simple_bus fwd_bus);
    consumer_mod u_cons (.bus(fwd_bus));
endmodule

// Two children forward the same port through two conflicting modports --
// genuinely ambiguous, must not guess.
module fwd_ambiguous (simple_bus fwd_bus);
    producer_mod u_prod (.bus(fwd_bus));
    consumer_mod u_cons (.bus(fwd_bus));
endmodule

module top;
    simple_bus b1();
    simple_bus b2();
    fwd_single     u_single    (.fwd_bus(b1));
    fwd_ambiguous  u_ambiguous (.fwd_bus(b2));
endmodule
