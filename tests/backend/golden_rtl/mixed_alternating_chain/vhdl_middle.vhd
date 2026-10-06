library ieee;
use ieee.std_logic_1164.all;

entity vhdl_middle is
    port (
        clk : in  std_logic;
        a   : in  std_logic_vector(7 downto 0);
        y   : out std_logic_vector(7 downto 0)
    );
end entity vhdl_middle;

architecture rtl of vhdl_middle is
    component verilog_innermost is
        port (
            clk : in  std_logic;
            a   : in  std_logic_vector(7 downto 0);
            y   : out std_logic_vector(7 downto 0)
        );
    end component verilog_innermost;
begin
    u_inner : verilog_innermost
        port map (clk => clk, a => a, y => y);
end architecture rtl;
