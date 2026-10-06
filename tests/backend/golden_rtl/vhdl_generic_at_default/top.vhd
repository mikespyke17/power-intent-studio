library ieee;
use ieee.std_logic_1164.all;

entity top is
    port (
        clk : in  std_logic;
        d   : in  std_logic_vector(7 downto 0);
        q   : out std_logic_vector(7 downto 0)
    );
end entity top;

architecture structural of top is
begin
    -- No generic map at all -- WIDTH falls back to its declared default (8).
    u_sub : entity work.sub
        port map (clk => clk, d => d, q => q);
end architecture structural;
