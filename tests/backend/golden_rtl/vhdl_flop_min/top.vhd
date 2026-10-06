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
begin
    u_sub : entity work.sub
        port map (clk => clk, rst_n => rst_n, d => data_in, q => data_out);
end architecture rtl;
