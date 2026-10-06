library ieee;
use ieee.std_logic_1164.all;

entity sub is
    port (
        clk : in  std_logic;
        sel : in  std_logic;
        a   : in  std_logic_vector(7 downto 0);
        b   : in  std_logic_vector(7 downto 0);
        q   : out std_logic_vector(7 downto 0)
    );
end entity sub;

architecture rtl of sub is
begin
    process (clk)
    begin
        if rising_edge(clk) then
            if sel = '1' then
                q <= a;
            else
                q <= b;
            end if;
        end if;
    end process;
end architecture rtl;
