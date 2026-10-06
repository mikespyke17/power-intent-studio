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

module top;
    simple_bus b();
    producer_mod u_prod (.bus(b));
    consumer_mod u_cons (.bus(b));
endmodule
