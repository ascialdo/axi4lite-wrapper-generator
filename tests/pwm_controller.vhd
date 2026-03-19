-- Example custom RTL: a simple PWM + status controller
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity pwm_controller is
  generic (
    CLK_FREQ_HZ : integer := 100_000_000;
    PWM_BITS    : integer := 8
  );
  port (
    -- Clock and reset (conventionally unmapped)
    clk           : in  std_logic;
    rst_n         : in  std_logic;

    -- Control inputs (written by CPU via AXI)
    enable        : in  std_logic;
    duty_cycle    : in  std_logic_vector(7 downto 0);
    prescaler     : in  std_logic_vector(15 downto 0);
    irq_mask      : in  std_logic_vector(3 downto 0);

    -- Status outputs (read by CPU via AXI)
    pwm_out       : out std_logic;
    period_done   : out std_logic;
    irq_status    : out std_logic_vector(3 downto 0);
    cycle_count   : out std_logic_vector(31 downto 0)
  );
end entity pwm_controller;

architecture rtl of pwm_controller is
begin
  -- (implementation omitted — this file is parsed, not simulated)
end architecture rtl;
