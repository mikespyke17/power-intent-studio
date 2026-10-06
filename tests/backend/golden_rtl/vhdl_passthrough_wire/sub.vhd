library ieee;
use ieee.std_logic_1164.all;

entity sub is
    port (
        a          : in  std_logic;
        b          : in  std_logic;
        y_passthrough : out std_logic;
        y_inverted    : out std_logic
    );
end entity sub;

architecture rtl of sub is
begin
    y_passthrough <= a;
    y_inverted <= not b;
end architecture rtl;
