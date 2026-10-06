library ieee;
use ieee.std_logic_1164.all;

entity multiproc is
    port (
        clk   : in  std_logic;
        rst_n : in  std_logic;
        a_in  : in  std_logic_vector(7 downto 0);
        b_in  : in  std_logic_vector(7 downto 0);
        a_out : out std_logic_vector(7 downto 0);
        b_out : out std_logic_vector(7 downto 0)
    );
end entity multiproc;

architecture rtl of multiproc is
begin
    process (clk, rst_n)
    begin
        if rst_n = '0' then
            a_out <= (others => '0');
        elsif rising_edge(clk) then
            a_out <= a_in;
        end if;
    end process;

    process (clk)
    begin
        if rising_edge(clk) then
            b_out <= b_in;
        end if;
    end process;
end architecture rtl;
