library ieee;
use ieee.std_logic_1164.all;
use work.pkg.all;

entity sub is
    port (
        clk : in  std_logic;
        d   : in  std_logic_vector(WIDTH-1 downto 0);
        q   : out std_logic_vector(WIDTH-1 downto 0)
    );
end entity sub;

architecture rtl of sub is
begin
    process (clk)
    begin
        if rising_edge(clk) then
            q <= d;
        end if;
    end process;
end architecture rtl;
