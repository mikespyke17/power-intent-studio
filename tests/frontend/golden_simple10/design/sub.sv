module sub (
    input  wire clk,
    input  wire rstn,
    input  wire [31:0] i_q,
    output logic [31:0] o_q
);

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            o_q <= '0;
        end else begin
            o_q <= i_q;
        end
    end

endmodule
