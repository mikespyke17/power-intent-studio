library ieee;
use ieee.std_logic_1164.all;

entity sub is
    port (
        en : in  std_logic;
        d  : in  std_logic_vector(7 downto 0);
        q  : out std_logic_vector(7 downto 0)
    );
end entity sub;

architecture rtl of sub is
begin
    process (en, d)
    begin
        if en = '1' then
            q <= d;
        end if;
    end process;
end architecture rtl;
