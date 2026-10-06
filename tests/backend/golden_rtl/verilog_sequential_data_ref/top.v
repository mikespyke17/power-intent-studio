module top (
    input clk, input rst_n,
    input [7:0] a, input [7:0] b, input sel,
    input [3:0] narrow,
    output logic [7:0] q_clean,
    output logic [7:0] q_mux,
    output logic [7:0] q_op,
    output logic [7:0] q_widen,
    output logic [7:0] q_resetless
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            q_clean <= 0;
            q_mux   <= 0;
            q_op    <= 0;
            q_widen <= 0;
        end else begin
            q_clean <= a;                 // clean bare pass-through -> data_ref == "a"
            if (sel) q_mux <= a; else q_mux <= b;  // two DIFFERENT bare sources -> data_ref is None
            q_op <= a + b;                 // real logic (an operator) -> data_ref is None
            q_widen <= narrow;             // width mismatch (4-bit -> 8-bit) -> data_ref is None
        end
    end

    always_ff @(posedge clk) begin
        q_resetless <= a;                  // resetless block, still a clean pass-through -> data_ref == "a"
    end
endmodule
