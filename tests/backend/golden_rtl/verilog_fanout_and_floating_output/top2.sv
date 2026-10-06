module top2 (
    input  wire         clk,
    input  wire         reset_n,
    input  wire [31:0]  d,
    output logic [31:0] q,
    input  wire         i_iso,
    output wire         o_iso,
    input  wire         i_sleep,
    output wire         o_sleep
);
  
    logic [31:0] q_int;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            q_int <= '0;
        end else begin
            q_int <= d;
        end
    end

    logic sleep_int;
    sub21 u_sub21 (
        .i_q      (q_int),
        .o_q      (q),
        .i_iso    (i_iso),
        .i_sleep  (i_sleep),
        .o_iso    (o_iso),
        .o_sleep  (sleep_int)
    );

    sub22 u_sub22 (
        .i_sleep  (i_sleep),
        .o_sleep  ()
    );

    assign o_sleep = ~sleep_int;



endmodule