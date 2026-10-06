library ieee;
use ieee.std_logic_1164.all;

-- Note: this used to be "entity old_counter is" before a rename.
entity commented is
    generic (
        WIDTH : integer := 8
    );
    port (
        clk : in  std_logic;
        d   : in  std_logic_vector(WIDTH-1 downto 0);
        q   : out std_logic_vector(WIDTH-1 downto 0)
    );
end entity commented;

architecture rtl of commented is
begin
    process (clk)
    begin
        if rising_edge(clk) then
            q <= d;
        end if;
    end process;
end architecture rtl;
