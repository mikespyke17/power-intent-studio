module sram (
    input  wire        clk,
    input  wire        cen_b,
    input  wire        wen_b,
    input  wire        sleep_b,
    input  wire        iso_en,
    input  wire [7:0]  a,
    input  wire [31:0] d,
    output wire [31:0] q,
    output wire        power_good
);
    
    FAKE_SRAM_256X32 u_sram (
        .CLK        (clk),
        .CEN_B      (cen_b),
        .WEN_B      (wen_b),
        .SLEEP_B    (sleep_b),
        .ISO_EN     (iso_en),
        .A          (a),
        .D          (d),
        .Q          (q),
        .POWER_GOOD (power_good)
    );

endmodule
