"""
Unit tests for the AXI4-Lite wrapper generator.

Run with:  python -m pytest tests/test_pipeline.py -v
       or:  python tests/test_pipeline.py
"""
import sys, os, json, tempfile, unittest
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ir.model import AccessType, Direction, Field, IR, Port, Readback, Register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_port(name, direction='in', width=1, vhdl_type='std_logic'):
    return Port(name=name, direction=Direction(direction),
                width=width, vhdl_type=vhdl_type)

def _make_std_vhd(entity_name='my_dut', extra_ports='') -> str:
    return f"""
library ieee;
use ieee.std_logic_1164.all;

entity {entity_name} is
  port (
    clk      : in  std_logic;
    rst_n    : in  std_logic;
    ctrl_reg : in  std_logic_vector(7 downto 0);
    status   : out std_logic_vector(3 downto 0);
    flag     : out std_logic{';' + extra_ports if extra_ports else ''}
  );
end entity {entity_name};
architecture rtl of {entity_name} is begin end architecture rtl;
"""

def _make_std_json(register_width=32) -> dict:
    return {
        "register_width": register_width,
        "registers": {
            "REG_CTRL": {
                "offset": "0x00",
                "fields": [
                    {"port": "ctrl_reg", "bits": [7, 0], "access": "RW"}
                ]
            },
            "REG_STATUS": {
                "offset": "0x04",
                "fields": [
                    {"port": "status",   "bits": [3, 0], "access": "RO"},
                    {"port": "flag",     "bits": [4, 4], "access": "RO"}
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Stage 1A — VHDL parser
# ---------------------------------------------------------------------------

class TestVHDLParser(unittest.TestCase):

    def _parse(self, vhdl: str):
        from parser.vhdl_parser import parse_entity
        with tempfile.NamedTemporaryFile(suffix='.vhd', mode='w', delete=False) as f:
            f.write(vhdl); name = f.name
        try:
            return parse_entity(name)
        finally:
            os.unlink(name)

    def test_basic_entity(self):
        name, ports, generics = self._parse(_make_std_vhd())
        self.assertEqual(name, 'my_dut')
        self.assertEqual(len(ports), 5)

    def test_port_directions(self):
        _, ports, _ = self._parse(_make_std_vhd())
        pmap = {p.name: p for p in ports}
        self.assertEqual(pmap['clk'].direction, Direction.IN)
        self.assertEqual(pmap['status'].direction, Direction.OUT)

    def test_std_logic_width(self):
        _, ports, _ = self._parse(_make_std_vhd())
        pmap = {p.name: p for p in ports}
        self.assertEqual(pmap['clk'].width, 1)
        self.assertEqual(pmap['flag'].width, 1)

    def test_slv_width(self):
        _, ports, _ = self._parse(_make_std_vhd())
        pmap = {p.name: p for p in ports}
        self.assertEqual(pmap['ctrl_reg'].width, 8)
        self.assertEqual(pmap['status'].width, 4)

    def test_multi_name_port(self):
        vhdl = """
entity multi is
  port (
    a, b, c : in std_logic;
    d       : out std_logic_vector(3 downto 0)
  );
end entity multi;
architecture rtl of multi is begin end architecture rtl;
"""
        _, ports, _ = self._parse(vhdl)
        names = [p.name for p in ports]
        self.assertIn('a', names)
        self.assertIn('b', names)
        self.assertIn('c', names)
        self.assertEqual(len([p for p in ports if p.name in ('a','b','c')]), 3)

    def test_generics_extracted(self):
        vhdl = """
entity gen_dut is
  generic (
    DATA_WIDTH : integer := 8;
    DEPTH      : integer := 16
  );
  port (
    clk : in std_logic
  );
end entity gen_dut;
architecture rtl of gen_dut is begin end architecture rtl;
"""
        _, _, generics = self._parse(vhdl)
        names = [g.name for g in generics]
        self.assertIn('DATA_WIDTH', names)
        self.assertIn('DEPTH', names)
        dw = next(g for g in generics if g.name == 'DATA_WIDTH')
        self.assertEqual(dw.vhdl_type, 'integer')
        self.assertEqual(dw.default, '8')

    def test_unsupported_type_raises(self):
        from parser.vhdl_parser import ParseError
        vhdl = """
entity bad is
  port (
    x : in integer
  );
end entity bad;
architecture rtl of bad is begin end architecture rtl;
"""
        with self.assertRaises(ParseError):
            self._parse(vhdl)

    def test_generic_dependent_port_width_is_none(self):
        """A port whose width uses a generic name must have width=None."""
        vhdl = """
entity gdut is
  generic (
    DATA_WIDTH : integer := 8
  );
  port (
    clk      : in  std_logic;
    data_out : out std_logic_vector(DATA_WIDTH-1 downto 0)
  );
end entity gdut;
architecture rtl of gdut is begin end architecture rtl;
"""
        _, ports, _ = self._parse(vhdl)
        pmap = {p.name: p for p in ports}
        self.assertIsNone(pmap['data_out'].width)
        self.assertTrue(pmap['data_out'].is_generic_dependent)
        # clk is still resolved normally
        self.assertEqual(pmap['clk'].width, 1)

    def test_generic_dependent_port_vhdl_type_preserved(self):
        """The raw vhdl_type string must be preserved verbatim for pass-through."""
        vhdl = """
entity gdut is
  generic (
    W : integer := 16
  );
  port (
    bus_out : out std_logic_vector(W-1 downto 0)
  );
end entity gdut;
architecture rtl of gdut is begin end architecture rtl;
"""
        _, ports, _ = self._parse(vhdl)
        self.assertEqual(ports[0].vhdl_type, 'std_logic_vector(W-1 downto 0)')

    def test_no_entity_raises(self):
        from parser.vhdl_parser import ParseError
        with self.assertRaises(ParseError):
            self._parse("-- just a comment\n")

    def test_large_slv(self):
        vhdl = """
entity wide is
  port (
    bus64 : in std_logic_vector(63 downto 0)
  );
end entity wide;
architecture rtl of wide is begin end architecture rtl;
"""
        _, ports, _ = self._parse(vhdl)
        self.assertEqual(ports[0].width, 64)


# ---------------------------------------------------------------------------
# Stage 1B + 2 — JSON validator & IR builder
# ---------------------------------------------------------------------------

class TestJSONValidator(unittest.TestCase):

    def _build(self, vhdl_str, json_dict):
        from parser.vhdl_parser import parse_entity
        from parser.json_validator import build_ir
        with tempfile.NamedTemporaryFile(suffix='.vhd', mode='w', delete=False) as f:
            f.write(vhdl_str); vhd_name = f.name
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', delete=False) as f:
            json.dump(json_dict, f); json_name = f.name
        try:
            name, ports, generics = parse_entity(vhd_name)
            return build_ir(json_name, name, ports, generics)
        finally:
            os.unlink(vhd_name); os.unlink(json_name)

    def _build_fails(self, vhdl_str, json_dict):
        from parser.json_validator import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._build(vhdl_str, json_dict)
        return ctx.exception

    def test_valid_build(self):
        ir = self._build(_make_std_vhd(), _make_std_json())
        self.assertEqual(ir.entity_name, 'my_dut')
        self.assertEqual(len(ir.registers), 2)

    def test_register_order_by_offset(self):
        d = _make_std_json()
        # Swap order in dict — should still sort by offset
        d['registers'] = {k: d['registers'][k]
                          for k in reversed(list(d['registers']))}
        ir = self._build(_make_std_vhd(), d)
        offsets = [r.offset for r in ir.registers]
        self.assertEqual(offsets, sorted(offsets))

    def test_unknown_port_error(self):
        d = _make_std_json()
        d['registers']['REG_CTRL']['fields'][0]['port'] = 'nonexistent'
        exc = self._build_fails(_make_std_vhd(), d)
        self.assertTrue(any('nonexistent' in e for e in exc.errors))

    def test_width_mismatch_error(self):
        d = _make_std_json()
        # ctrl_reg is 8 bits, but we claim [3:0] = 4 bits
        d['registers']['REG_CTRL']['fields'][0]['bits'] = [3, 0]
        exc = self._build_fails(_make_std_vhd(), d)
        self.assertTrue(any('width' in e.lower() for e in exc.errors))

    def test_bit_overlap_error(self):
        d = _make_std_json()
        d['registers']['REG_STATUS']['fields'].append(
            {"port": "flag", "bits": [3, 3], "access": "RO"}  # overlaps with status[3:0]
        )
        exc = self._build_fails(_make_std_vhd(), d)
        self.assertTrue(any('overlap' in e for e in exc.errors))

    def test_misaligned_offset_error(self):
        d = _make_std_json()
        d['registers']['REG_CTRL']['offset'] = '0x01'
        exc = self._build_fails(_make_std_vhd(), d)
        self.assertTrue(any('aligned' in e for e in exc.errors))

    def test_duplicate_offset_error(self):
        d = _make_std_json()
        d['registers']['REG_STATUS']['offset'] = '0x00'  # same as REG_CTRL
        exc = self._build_fails(_make_std_vhd(), d)
        self.assertTrue(any('conflict' in e for e in exc.errors))

    def test_ports_marked_as_mapped(self):
        ir = self._build(_make_std_vhd(), _make_std_json())
        pmap = {p.name: p for p in ir.ports}
        self.assertTrue(pmap['ctrl_reg'].mapped)
        self.assertTrue(pmap['status'].mapped)
        self.assertFalse(pmap['clk'].mapped)   # clock is not in regmap

    def test_invalid_access_type(self):
        d = _make_std_json()
        d['registers']['REG_CTRL']['fields'][0]['access'] = 'XY'
        exc = self._build_fails(_make_std_vhd(), d)
        self.assertTrue(any('access' in e.lower() for e in exc.errors))

    def test_generic_dependent_port_cannot_be_mapped(self):
        """JSON that maps a generic-dependent port must be rejected with a clear error."""
        vhdl = """
entity gdut is
  generic (
    DATA_WIDTH : integer := 8
  );
  port (
    clk      : in  std_logic;
    data_out : out std_logic_vector(DATA_WIDTH-1 downto 0)
  );
end entity gdut;
architecture rtl of gdut is begin end architecture rtl;
"""
        json_map = {
            "register_width": 32,
            "registers": {
                "REG0": {
                    "offset": "0x00",
                    "fields": [
                        {"port": "data_out", "bits": [7, 0], "access": "RO"}
                    ]
                }
            }
        }
        exc = self._build_fails(vhdl, json_map)
        self.assertTrue(any('generic-dependent' in e for e in exc.errors))
        self.assertTrue(any('data_out' in e for e in exc.errors))

    def test_register_width_64(self):
        d = _make_std_json(register_width=64)
        # Make ctrl_reg 64 bits to match
        vhdl = _make_std_vhd().replace(
            'ctrl_reg : in  std_logic_vector(7 downto 0)',
            'ctrl_reg : in  std_logic_vector(63 downto 0)'
        )
        d['registers']['REG_CTRL']['fields'][0]['bits'] = [63, 0]
        ir = self._build(vhdl, d)
        self.assertEqual(ir.register_width, 64)


# ---------------------------------------------------------------------------
# Stage 3 — Semantic checks
# ---------------------------------------------------------------------------

class TestSemanticChecker(unittest.TestCase):

    def _ir_from_str(self, vhdl_str, json_dict):
        from parser.vhdl_parser import parse_entity
        from parser.json_validator import build_ir
        with tempfile.NamedTemporaryFile(suffix='.vhd', mode='w', delete=False) as f:
            f.write(vhdl_str); vhd = f.name
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', delete=False) as f:
            json.dump(json_dict, f); jsn = f.name
        try:
            n, p, g = parse_entity(vhd)
            return build_ir(jsn, n, p, g)
        finally:
            os.unlink(vhd); os.unlink(jsn)

    def test_direction_warning_out_ro_is_valid(self):
        from checks.semantic import run_checks
        ir = self._ir_from_str(_make_std_vhd(), _make_std_json())
        # status is DUT 'out' mapped as RO — CORRECT mapping, no warning expected
        warnings = run_checks(ir)
        self.assertFalse(any('status' in w and 'RO' in w for w in warnings))

    def test_direction_warning_in_ro(self):
        from checks.semantic import run_checks
        # ctrl_reg is DUT 'in' mapped as RO — wrong, should warn
        d = _make_std_json()
        d['registers']['REG_CTRL']['fields'][0]['access'] = 'RO'
        ir = self._ir_from_str(_make_std_vhd(), d)
        warnings = run_checks(ir)
        self.assertTrue(any('ctrl_reg' in w and 'RO' in w for w in warnings))

    def test_direction_warning_out_rw(self):
        from checks.semantic import run_checks
        # status is DUT 'out' mapped as RW — wrong, should warn
        d = _make_std_json()
        d['registers']['REG_STATUS']['fields'][0]['access'] = 'RW'
        ir = self._ir_from_str(_make_std_vhd(), d)
        warnings = run_checks(ir)
        self.assertTrue(any('status' in w and 'RW' in w for w in warnings))

    def test_unmapped_clock_no_warning(self):
        from checks.semantic import run_checks
        ir = self._ir_from_str(_make_std_vhd(), _make_std_json())
        warnings = run_checks(ir)
        # clk and rst_n should NOT appear in warnings (filtered as clock/reset)
        self.assertFalse(any('clk' in w for w in warnings))
        self.assertFalse(any('rst_n' in w for w in warnings))

    def test_unmapped_non_trivial_warns(self):
        from checks.semantic import run_checks
        # Add an extra port that is not in the regmap and not a clock/reset
        vhdl = _make_std_vhd(extra_ports='\n    debug_out : out std_logic')
        ir = self._ir_from_str(vhdl, _make_std_json())
        warnings = run_checks(ir)
        self.assertTrue(any('debug_out' in w for w in warnings))


# ---------------------------------------------------------------------------
# Stage 4 — Code generation
# ---------------------------------------------------------------------------

class TestCodegen(unittest.TestCase):

    def setUp(self):
        from parser.vhdl_parser import parse_entity
        from parser.json_validator import build_ir
        self.tmpdir = tempfile.mkdtemp()
        vhd_path = os.path.join(self.tmpdir, 'dut.vhd')
        jsn_path = os.path.join(self.tmpdir, 'regmap.json')
        Path(vhd_path).write_text(_make_std_vhd())
        Path(jsn_path).write_text(json.dumps(_make_std_json()))
        n, p, g = parse_entity(vhd_path)
        self.ir = build_ir(jsn_path, n, p, g)

    def _generate(self):
        from generator.codegen import generate
        out_dir = os.path.join(self.tmpdir, 'out')
        return generate(self.ir, out_dir)

    def test_both_files_created(self):
        outputs = self._generate()
        self.assertIn('axi_lite_if', outputs)
        self.assertIn('top_wrapper', outputs)
        self.assertTrue(outputs['axi_lite_if'].exists())
        self.assertTrue(outputs['top_wrapper'].exists())

    def test_axi_if_entity_present(self):
        outputs = self._generate()
        content = outputs['axi_lite_if'].read_text()
        self.assertIn('entity axi_lite_if is', content)
        self.assertIn('architecture rtl of axi_lite_if is', content)

    def test_top_wrapper_entity_name(self):
        outputs = self._generate()
        content = outputs['top_wrapper'].read_text()
        self.assertIn('entity my_dut_axi is', content)

    def test_instance_labels_correct(self):
        """Instance labels must be label : component_name, not u_component_name."""
        outputs = self._generate()
        content = outputs['top_wrapper'].read_text()
        self.assertIn('axi_lite_if_inst : axi_lite_if', content)
        self.assertIn('my_dut_inst : my_dut', content)
        self.assertNotIn('u_axi_lite_if', content)
        self.assertNotIn('u_my_dut', content)

    def test_generics_in_top_wrapper(self):
        """DUT generics must appear in top wrapper entity, component decl, and generic map."""
        from parser.vhdl_parser import parse_entity
        from parser.json_validator import build_ir
        import json, tempfile, os
        # Use the PWM fixture which has generics
        tests_dir = Path(__file__).parent
        vhd = str(tests_dir / 'pwm_controller.vhd')
        jsn = str(tests_dir / 'pwm_regmap.json')
        n, p, g = parse_entity(vhd)
        ir = build_ir(jsn, n, p, g)
        from generator.codegen import generate
        import tempfile
        out_dir = tempfile.mkdtemp()
        outputs = generate(ir, out_dir)
        content = outputs['top_wrapper'].read_text()
        # Generic must appear in entity generic section
        self.assertIn('CLK_FREQ_HZ', content)
        self.assertIn('PWM_BITS', content)
        # Generic map on DUT instance must pass them through
        self.assertIn('CLK_FREQ_HZ => CLK_FREQ_HZ', content)
        self.assertIn('PWM_BITS => PWM_BITS', content)

    def test_address_decode_present(self):
        outputs = self._generate()
        content = outputs['axi_lite_if'].read_text()
        self.assertIn('case loc_addr is', content)
        self.assertIn('when b"00"', content)  # REG_CTRL at 0x00 → word 0 → "00"
        self.assertIn('when b"01"', content)  # REG_STATUS at 0x04 → word 1 → "01"

    def test_ro_shadow_update(self):
        outputs = self._generate()
        content = outputs['axi_lite_if'].read_text()
        # status is RO → shadow updated inside clocked process
        self.assertIn('reg_status_reg', content)

    def test_signals_in_top_wrapper(self):
        outputs = self._generate()
        content = outputs['top_wrapper'].read_text()
        self.assertIn('sig_ctrl_reg', content)
        self.assertIn('sig_status', content)
        self.assertIn('sig_flag', content)


# ---------------------------------------------------------------------------
# IR model unit tests
# ---------------------------------------------------------------------------

class TestIRModel(unittest.TestCase):

    def test_field_width(self):
        f = Field('x', 7, 0, AccessType.RW)
        self.assertEqual(f.width, 8)

    def test_field_width_single_bit(self):
        f = Field('x', 3, 3, AccessType.RO)
        self.assertEqual(f.width, 1)

    def test_register_offset_hex(self):
        r = Register('R', offset=0x10)
        self.assertEqual(r.offset_hex, '0x0010')

    def test_ir_unmapped_ports(self):
        p1 = _make_port('a')
        p1.mapped = False
        p2 = _make_port('b')
        p2.mapped = True
        ir = IR(entity_name='x', ports=[p1, p2], registers=[])
        unmapped = ir.unmapped_ports()
        self.assertEqual(len(unmapped), 1)
        self.assertEqual(unmapped[0].name, 'a')

    def test_ir_port_by_name(self):
        p = _make_port('my_port')
        ir = IR(entity_name='x', ports=[p], registers=[])
        self.assertIs(ir.port_by_name('my_port'), p)
        self.assertIsNone(ir.port_by_name('missing'))

    def test_addr_bits_minimum(self):
        ir = IR(entity_name='x', ports=[], registers=[])
        self.assertGreaterEqual(ir.addr_bits, 4)

    def test_addr_bits_scales_with_registers(self):
        regs = [Register(f'R{i}', offset=i*4) for i in range(16)]
        ir = IR(entity_name='x', ports=[], registers=regs)
        # 16 regs × 4 bytes = 64-byte span → need at least 6 addr bits
        self.assertGreaterEqual(ir.addr_bits, 6)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
