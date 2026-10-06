module sub (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] d,
    output reg  [7:0] q
);
    always @(negedge rst_n or posedge clk) begin
        if (!rst_n) q <= 8'b0;
        else        q <= d;
    end
endmodule
