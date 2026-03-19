# AXI4-Lite Wrapper Generator

A Python CLI tool that automatically generates VHDL boilerplate to expose custom RTL entities over an AXI4-Lite bus. Given a VHDL entity and a JSON register map, it outputs two ready-to-use VHDL files.

## Output

- `axi_lite_if.vhd` — AXI4-Lite slave register interface with shadow registers
- `<entity_name>_axi.vhd` — Top-level wrapper that instantiates both the AXI interface and the original DUT
- `<entity_name>_regmap.md` — Markdown register map table (use `--no-docs` to skip)

---

## Modes

### Standalone Mode

Download the repo and run directly — no installation required.

```bash
python cli.py path/to/entity.vhd path/to/regmap.json --output-dir path/to/output
```

### Integration Mode (CI/CD)

Install the tool as a Python package, then call the `axi-wrapper-gen` command. Designed for automated pipelines where a repository containing an IP core triggers wrapper generation on every push.

#### 1. Install

```bash
pip install git+https://github.com/ascialdo/axi4lite-wrapper-generator
```

#### 2. Add `axi_wrapper.json` to your IP core repository

```json
{
  "rtl": "rtl/my_entity.vhd",
  "regmap": "my_entity_regmap.json",
  "output_dir": "generated"
}
```

#### 3. Run

```bash
axi-wrapper-gen
```

When called with no positional arguments, the tool reads `axi_wrapper.json` from the current directory. The `--output-dir` flag can override the config value.

You can also pass explicit paths (same as standalone):

```bash
axi-wrapper-gen rtl/my_entity.vhd my_entity_regmap.json --output-dir generated
```

---

## Register Map JSON Format

```json
{
  "register_width": 32,
  "registers": {
    "CONTROL": {
      "offset": "0x00",
      "description": "Main control register.",
      "fields": [
        { "port": "enable", "bits": [0, 0], "access": "RW", "description": "Enables the output." },
        { "port": "mode",   "bits": [2, 1], "access": "RW" },
        { "port": "status", "bits": [3, 3], "access": "RO", "readback": "LIVE" }
      ]
    }
  }
}
```

**Access types**: `RW` (read/write), `RO` (read-only), `WO` (write-only)
**Readback modes**: `SHADOW` (last written value, default) or `LIVE` (current DUT output)

The `description` field is optional on both registers and fields. When present, it is included in the generated `_regmap.md`.

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
