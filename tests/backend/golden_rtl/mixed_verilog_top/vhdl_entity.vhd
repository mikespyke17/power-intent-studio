library ieee;
use ieee.std_logic_1164.all;

entity vhdl_entity is
    port (
        clk   : in  std_logic;
        rst_n : in  std_logic;
        d     : in  std_logic_vector(7 downto 0);
        q     : out std_logic_vector(7 downto 0)
    );
end entity vhdl_entity;

architecture rtl of vhdl_entity is
begin
    process (clk, rst_n)
    begin
        if rst_n = '0' then
            q <= (others => '0');
        elsif rising_edge(clk) then
            q <= d;
        end if;
    end process;
end architecture rtl;
