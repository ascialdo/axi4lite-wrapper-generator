# CLAUDE.md — AXI4-Lite Wrapper Generator

Developer guide for Claude Code. Read this before making any changes.

---

## What this tool does

Takes a VHDL entity and a JSON register map (`axi_config.json`) and generates three files:
- `axi_lite_if.vhd` — AXI4-Lite slave register interface with shadow registers
- `<entity>_axi.vhd` — Top-level wrapper instantiating both the AXI interface and the DUT
- `<entity>_regmap.md` — Markdown register map documentation

Output is written to `<entity_name>_axi/` in the **same directory as the VHD file** specified in `top_entity`.

---

## Repository structure

```
cli.py                        # Entry point — orchestrates the 4-stage pipeline
pyproject.toml                # Package definition, installs axi-wrapper-gen command
ir/
  model.py                    # Dataclasses: IR, Register, Field, Port, Generic + enums
parser/
  vhdl_parser.py              # Regex-based VHDL entity parser (no external HDL tool)
  json_validator.py           # Validates axi_config.json and builds the IR
checks/
  semantic.py                 # Cross-checks: direction/access mismatches, unmapped ports
generator/
  codegen.py                  # Jinja2 renderer — produces all 3 output files
  templates/
    axi_lite_if.vhd.j2        # AXI4-Lite register interface template
    custom_rtl_axi.vhd.j2     # Top wrapper template
    regmap.md.j2              # Markdown register map template
tests/
  test_pipeline.py            # Full test suite (~42 tests)
  pwm_controller.vhd          # Test fixture: example DUT with generics
  pwm_regmap.json             # Test fixture: register map for PWM (legacy separate format)
```

---

## Pipeline stages

```
axi_config.json + (top_entity VHD)
        │
        ▼
[Stage 1A] vhdl_parser.py      → entity_name, ports[], generics[]
        │
        ▼
[Stage 1B+2] json_validator.py → validates JSON, builds IR (all errors collected at once)
        │
        ▼
[Stage 3] semantic.py          → warnings: unmapped ports, direction/access mismatches
        │
        ▼
[Stage 4] codegen.py           → renders 3 Jinja2 templates, writes output files
```

---

## Key API signatures

```python
# parser/vhdl_parser.py
parse_entity(vhd_path: str) -> tuple[str, list[Port], list[Generic]]

# parser/json_validator.py — NOTE: no vhdl_path, json_path is axi_config.json
build_ir(json_path, entity_name, ports, generics) -> IR

# generator/codegen.py
generate(ir: IR, output_dir: str | Path, generate_docs: bool = True) -> dict[str, Path]
# returns: {'regmap': Path, 'axi_lite_if': Path, 'top_wrapper': Path}

# checks/semantic.py
run_checks(ir: IR) -> list[str]  # returns warnings, raises SemanticError on hard failures
```

---

## axi_config.json format

Single file at the repo root — combines tool config and register map:

```json
{
  "top_entity": "rtl/my_entity.vhd",
  "register_width": 32,
  "registers": {
    "REG_NAME": {
      "offset": "0x00",
      "description": "Optional register description.",
      "fields": [
        {
          "port": "port_name",
          "bits": [7, 0],
          "access": "RW",
          "readback": "shadow",
          "description": "Optional field description."
        }
      ]
    }
  }
}
```

- `top_entity`: path to the VHD file, relative to `axi_config.json`
- `register_width`: `32` (default) or `64`
- `offset`: hex string, must be 4-byte aligned
- `access`: `RW`, `RO`, `WO`
- `readback`: `shadow` (default, last written value) or `live` (current DUT output)
- `description`: optional on both registers and fields, included in `_regmap.md`

---

## CLI

```bash
# Install
pip install git+https://github.com/ascialdo/axi4lite-wrapper-generator

# Run (reads axi_config.json from current directory)
axi-wrapper-gen

# Skip markdown doc generation
axi-wrapper-gen --no-docs

# Run directly without installing
python cli.py
```

No positional args. No `--output-dir`. The only flag is `--no-docs`.

---

## Running tests

```bash
python -m unittest tests/test_pipeline.py
```

All tests use `tempfile` for isolation. The PWM fixture (`tests/pwm_controller.vhd` + `tests/pwm_regmap.json`) is used for generics and codegen tests — paths are resolved with `Path(__file__).parent`.

---

## IR dataclasses — important notes

- `Field.description: str = ""` and `Register.description: str = ""` — always default to empty string, never None
- `Field` ordering: `port_name, bit_high, bit_low, access, readback, description`
- `Register` ordering: `name, offset, fields, description`
- `Port.is_generic_dependent` — True when width depends on a generic; such ports cannot be mapped
- `IR.addr_bits` — computed property, minimum bits to decode all registers

---

## Packaging notes

- `pyproject.toml` uses `setuptools.build_meta` (NOT `setuptools.backends.legacy:build` — that requires setuptools >=68.2 and breaks on many systems)
- `requires-python = ">=3.9"`
- `cli.py` must be declared under `[tool.setuptools] py-modules = ["cli"]` — it is a standalone module, not inside a package directory
- Templates are in `generator/templates/` and included via `[tool.setuptools.package-data] generator = ["templates/*.j2"]`

---

## .gitignore rules

Generated output folders (`*_axi/`, `output/`) are gitignored. Never commit generated VHDL files to this repo.

---

## Open items

- PR #2 (`fix/simplify-readme`) was merged — README simplified
- Next planned feature: CI/CD workflow for IP repos (repo X installs this tool on push, generates wrapper, commits back)
- No tests yet specifically for the unified `axi_config.json` flow end-to-end through `cli.main()`
