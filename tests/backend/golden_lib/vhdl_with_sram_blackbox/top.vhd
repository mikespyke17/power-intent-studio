library ieee;
use ieee.std_logic_1164.all;

entity top is
    port (
        clk  : in    std_logic;
        we   : in    std_logic;
        addr : in    std_logic_vector(7 downto 0);
        data : inout std_logic_vector(7 downto 0)
    );
end entity top;

architecture rtl of top is
    component SRAM_256x8 is
        port (
            clk  : in    std_logic;
            we   : in    std_logic;
            addr : in    std_logic_vector(7 downto 0);
            data : inout std_logic_vector(7 downto 0)
        );
    end component SRAM_256x8;
begin
    u_sram : SRAM_256x8
        port map (clk => clk, we => we, addr => addr, data => data);
end architecture rtl;
