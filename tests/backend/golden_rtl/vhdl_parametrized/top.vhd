library ieee;
use ieee.std_logic_1164.all;

entity top is
    port (
        clk        : in  std_logic;
        rst_n      : in  std_logic;
        data_in_a  : in  std_logic_vector(7 downto 0);
        data_in_b  : in  std_logic_vector(15 downto 0)
    );
end entity top;

architecture structural of top is
begin
    u_sub_a : entity work.sub
        generic map (WIDTH => 8)
        port map (clk => clk, rst_n => rst_n, d => data_in_a, q => open);
    u_sub_b : entity work.sub
        generic map (WIDTH => 16)
        port map (clk => clk, rst_n => rst_n, d => data_in_b, q => open);
end architecture structural;
