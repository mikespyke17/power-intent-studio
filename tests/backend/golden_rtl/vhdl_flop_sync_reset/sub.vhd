library ieee;
use ieee.std_logic_1164.all;

entity sub is
    port (
        clk   : in  std_logic;
        rst_n : in  std_logic;
        d     : in  std_logic_vector(7 downto 0);
        q     : out std_logic_vector(7 downto 0)
    );
end entity sub;

architecture rtl of sub is
begin
    process (clk)
    begin
        if rising_edge(clk) then
            if rst_n = '0' then
                q <= (others => '0');
            else
                q <= d;
            end if;
        end if;
    end process;
end architecture rtl;
