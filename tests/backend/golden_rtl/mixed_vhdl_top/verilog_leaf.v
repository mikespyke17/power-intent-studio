module verilog_leaf (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] a,
    output wire [7:0] y
);
    reg [7:0] q;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 8'b0;
        else        q <= a;
    end
    assign y = q;
endmodule
