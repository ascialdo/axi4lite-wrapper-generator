# AXI4-Lite Wrapper Generator

A Python CLI tool that automatically generates VHDL boilerplate to expose custom RTL entities over an AXI4-Lite bus. Reads a single `axi_config.json` file and produces three output files.

## Output

Generated into `<entity_name>_axi/` in the same directory as the VHD file:

- `axi_lite_if.vhd` — AXI4-Lite slave register interface with shadow registers
- `<entity_name>_axi.vhd` — Top-level wrapper that instantiates both the AXI interface and the original DUT
- `<entity_name>_regmap.md` — Markdown register map table (skip with `--no-docs`)

---

## Usage

#### 1. Install

```bash
pip install git+https://github.com/ascialdo/axi4lite-wrapper-generator
```

#### 2. Add `axi_config.json` to the root of your IP core repository

```json
{
  "top_entity": "rtl/my_entity.vhd",
  "register_width": 32,
  "registers": {
    "REG_CTRL": {
      "offset": "0x00",
      "description": "Main control register.",
      "fields": [
        { "port": "enable", "bits": [0, 0], "access": "RW", "description": "Enables the output." },
        { "port": "mode",   "bits": [2, 1], "access": "RW" },
        { "port": "status", "bits": [3, 3], "access": "RO", "readback": "live" }
      ]
    }
  }
}
```

#### 3. Run

```bash
axi-wrapper-gen
```

The tool reads `axi_config.json` from the current directory. No other arguments are required.

---

## axi_config.json Reference

| Key | Required | Description |
|-----|----------|-------------|
| `top_entity` | yes | Path to the VHDL source file, relative to `axi_config.json` |
| `register_width` | no | AXI data bus width: `32` (default) or `64` |
| `registers` | yes | Register map object (see below) |

**Register fields:**

| Key | Required | Description |
|-----|----------|-------------|
| `offset` | yes | Byte offset as hex string, e.g. `"0x00"`. Must be 4-byte aligned. |
| `description` | no | Description included in the generated `_regmap.md` |
| `fields` | yes | List of field objects |

**Field keys:**

| Key | Required | Description |
|-----|----------|-------------|
| `port` | yes | Port name from the VHDL entity |
| `bits` | yes | `[high, low]` bit positions within the register |
| `access` | yes | `RW`, `RO`, or `WO` |
| `readback` | no | `shadow` (default) or `live` |
| `description` | no | Description included in the generated `_regmap.md` |

**Access types**: `RW` (read/write), `RO` (read-only), `WO` (write-only)
**Readback modes**: `shadow` — reads return last written value; `live` — reads return current DUT output

---

## Key Modules

| File | Role |
|------|------|
| `cli.py` | Entry point, orchestrates the pipeline, pretty output |
| `ir/model.py` | Dataclasses: `IR`, `Register`, `Field`, `Port`, `Generic` with enums `AccessType` (RW/RO/WO), `Direction`, `Readback` |
| `parser/vhdl_parser.py` | Regex-based VHDL parser — no external toolchain needed |
| `parser/json_validator.py` | JSON schema validation + IR builder; collects all errors before raising |
| `checks/semantic.py` | Warns on unmapped ports, direction/access mismatches |
| `generator/codegen.py` | Jinja2 renderer with custom filters (`ljust`, `bin_addr`) |
| `generator/templates/*.j2` | Two Jinja2 VHDL templates for the AXI interface and the top wrapper |

---

## Limitations

- Only `std_logic` and `std_logic_vector(N downto M)` port types supported — no `integer`, `unsigned`, etc.
- Registers always reset to 0 (no reset value specification in JSON)
- No support for AXI handshake backpressure or multi-cycle operations

---

## Dependencies

- Python >= 3.9
- [`jinja2`](https://jinja.palletsprojects.com/) >= 3.0

```bash
pip install jinja2
```

## Tests

```bash
python -m unittest tests/test_pipeline.py
```

The test suite covers all 4 pipeline stages (~35 unit tests) using `tests/pwm_controller.vhd` and `tests/pwm_regmap.json` as fixtures.
