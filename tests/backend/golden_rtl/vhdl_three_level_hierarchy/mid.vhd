library ieee;
use ieee.std_logic_1164.all;

entity mid is
    port (
        clk   : in  std_logic;
        rst_n : in  std_logic;
        d     : in  std_logic_vector(7 downto 0);
        q     : out std_logic_vector(7 downto 0)
    );
end entity mid;

architecture rtl of mid is
begin
    u_leaf : entity work.leaf
        port map (clk => clk, rst_n => rst_n, d => d, q => q);
end architecture rtl;
