# AXI4-Lite Wrapper Generator

A Python CLI tool that automatically generates VHDL boilerplate to expose custom RTL entities over an AXI4-Lite bus. Given a VHDL entity and a JSON register map, it outputs two ready-to-use VHDL files.

## Usage

```bash
python cli.py path/custom.vhd path/custom_regmap.json --output-dir path/output_folder
```

## Output

- `axi_lite_if.vhd` — AXI4-Lite slave register interface with shadow registers
- `<entity_name>_axi.vhd` — Top-level wrapper that instantiates both the AXI interface and the original DUT

## Architecture: 4-Stage Pipeline

```
VHDL file + JSON regmap
        │
        ▼
[Stage 1A] vhdl_parser.py     → extracts entity name, ports, generics (regex-based)
        │
        ▼
[Stage 1B+2] json_validator.py → validates JSON schema, builds IR (all errors at once)
        │
        ▼
[Stage 3] semantic.py          → cross-checks (warnings: direction mismatch, unmapped ports)
        │
        ▼
[Stage 4] codegen.py           → Jinja2 renders two VHDL files
        │
        ▼
axi_lite_if.vhd  +  <entity>_axi.vhd
```

## Key Modules

| File | Role |
|------|------|
| `cli.py` | Entry point, orchestrates the pipeline, pretty output |
| `ir/model.py` | Dataclasses: `IR`, `Register`, `Field`, `Port`, `Generic` with enums `AccessType` (RW/RO/WO), `Direction`, `Readback` |
| `parser/vhdl_parser.py` | Regex-based VHDL parser — no external toolchain needed |
| `parser/json_validator.py` | JSON schema validation + IR builder; collects all errors before raising |
| `checks/semantic.py` | Warns on unmapped ports, direction/access mismatches |
| `generator/codegen.py` | Jinja2 renderer with custom filters (`ljust`, `bin_addr`) |
| `templates/*.j2` | Two Jinja2 VHDL templates for the AXI interface and the top wrapper |

## Register Map JSON Format

```json
{
  "register_width": 32,
  "registers": {
    "CONTROL": {
      "offset": "0x00",
      "fields": [
        { "port": "enable",    "bits": [0, 0], "access": "RW" },
        { "port": "mode",      "bits": [2, 1], "access": "RW" },
        { "port": "status",    "bits": [3, 3], "access": "RO", "readback": "LIVE" }
      ]
    }
  }
}
```

**Access types**: `RW` (read/write), `RO` (read-only), `WO` (write-only)
**Readback modes**: `SHADOW` (last written value, default) or `LIVE` (current DUT output)

## Key Design Choices

- **Shadow registers**: RW/WO fields are buffered in shadow registers; RO fields sample the DUT output every cycle
- **No external HDL tool**: Parsing is entirely regex-based — no Vivado, Quartus, or GHDL required
- **Generic-dependent ports**: Detected and excluded from register mapping (width cannot be statically resolved)
- **Error aggregation**: All validation errors collected and shown at once, not one at a time

## Limitations

- Only `std_logic` and `std_logic_vector(N downto M)` port types supported — no `integer`, `unsigned`, etc.
- Registers always reset to 0 (no reset value specification in JSON)
- No support for AXI handshake backpressure or multi-cycle operations

## Dependencies

- Python 3.x (stdlib)
- [`jinja2`](https://jinja.palletsprojects.com/)

```bash
pip install jinja2
```

## Tests

```bash
python -m unittest tests/test_pipeline.py
```

The test suite covers all 4 pipeline stages (~35 unit tests) using `tests/pwm_controller.vhd` and `tests/pwm_regmap.json` as fixtures.
