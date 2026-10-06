library ieee;
use ieee.std_logic_1164.all;

entity top is
    port (
        clk      : in  std_logic;
        rst_n    : in  std_logic;
        data_in  : in  std_logic_vector(7 downto 0);
        data_out : out std_logic_vector(7 downto 0)
    );
end entity top;

architecture rtl of top is
    component verilog_leaf is
        port (
            clk   : in  std_logic;
            rst_n : in std_logic;
            a     : in  std_logic_vector(7 downto 0);
            y     : out std_logic_vector(7 downto 0)
        );
    end component verilog_leaf;
begin
    u_leaf : verilog_leaf
        port map (clk => clk, rst_n => rst_n, a => data_in, y => data_out);
end architecture rtl;
