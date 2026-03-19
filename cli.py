#!/usr/bin/env python3
"""
axi_wrapper — Automated AXI4-Lite wrapper generator for custom RTL.

Usage:
    axi-wrapper-gen [--no-docs]

Requires axi_config.json in the current directory. Example:

    {
      "top_entity": "rtl/my_entity.vhd",
      "register_width": 32,
      "registers": {
        "REG_CTRL": {
          "offset": "0x00",
          "fields": [
            { "port": "enable", "bits": [0, 0], "access": "RW" }
          ]
        }
      }
    }

Outputs (written to <top_entity_dir>/<entity_name>_axi/):
    axi_lite_if.vhd       — AXI4-Lite slave register interface
    <entity>_axi.vhd      — Top wrapper connecting DUT to AXI interface
    <entity>_regmap.md    — Register map documentation (skipped with --no-docs)
"""
from __future__ import annotations

import json
import sys
import argparse
import traceback
from pathlib import Path


# ── colour helpers (no external deps) ────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def bold(t):    return _c("1",     t)
def green(t):   return _c("32",   t)
def yellow(t):  return _c("33",   t)
def red(t):     return _c("31",   t)
def cyan(t):    return _c("36",   t)
def dim(t):     return _c("2",    t)


def print_banner():
    print()
    print(bold(cyan("  ╔══════════════════════════════════════════╗")))
    print(bold(cyan("  ║         AXI4-Lite Wrapper Generator      ║")))
    print(bold(cyan("  ╚══════════════════════════════════════════╝")))
    print()


def print_section(title: str):
    print(f"\n  {bold(title)}")
    print(f"  {'─' * len(title)}")


def print_ok(msg: str):
    print(f"  {green('✓')} {msg}")

def print_warn(msg: str):
    print(f"  {yellow('⚠')} {msg}")

def print_err(msg: str):
    print(f"  {red('✗')} {msg}")

def print_info(msg: str):
    print(f"  {dim('·')} {msg}")


# ── register map summary table ────────────────────────────────────────────────

def print_register_table(ir):
    from ir.model import AccessType
    print_section("Register Map Summary")

    col_w = [12, 8, 10, 6, 8, 8]  # reg, offset, port, bits, access, width
    header = ["Register", "Offset", "Port", "Bits", "Access", "Width"]

    def row_str(cells):
        return "  │ " + " │ ".join(
            str(c).ljust(col_w[i]) for i, c in enumerate(cells)
        ) + " │"

    sep = "  ├─" + "─┼─".join("─" * w for w in col_w) + "─┤"
    top = "  ┌─" + "─┬─".join("─" * w for w in col_w) + "─┐"
    bot = "  └─" + "─┴─".join("─" * w for w in col_w) + "─┘"

    print(top)
    print(bold(row_str(header)))
    print(sep)

    for reg in ir.registers:
        for i, fld in enumerate(reg.fields):
            reg_label  = reg.name if i == 0 else ""
            off_label  = reg.offset_hex if i == 0 else ""
            bits_label = f"[{fld.bit_high}:{fld.bit_low}]"
            access_col = fld.access.value
            if fld.access == AccessType.RW:
                access_col = yellow(access_col) if _USE_COLOR else access_col
            elif fld.access == AccessType.RO:
                access_col = cyan(access_col) if _USE_COLOR else access_col
            elif fld.access == AccessType.WO:
                access_col = dim(access_col) if _USE_COLOR else access_col
            print(row_str([reg_label, off_label, fld.port_name, bits_label,
                           access_col, f"{fld.width} bit{'s' if fld.width > 1 else ''}"]))
        print(sep)

    print(bot)


# ── config loader ─────────────────────────────────────────────────────────────

_CONFIG_FILE = 'axi_config.json'

def _load_config() -> tuple[str, Path]:
    """
    Read axi_config.json from the current directory.
    Returns (rtl_path, config_path).
    """
    cfg_path = Path(_CONFIG_FILE)
    if not cfg_path.exists():
        print_err(f"'{_CONFIG_FILE}' not found in current directory.")
        print_info("Create it with at minimum: top_entity, register_width, registers")
        sys.exit(1)

    try:
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        print_err(f"Failed to parse '{_CONFIG_FILE}': {e}")
        sys.exit(1)

    if 'top_entity' not in cfg:
        print_err(f"'{_CONFIG_FILE}' is missing required key: top_entity")
        sys.exit(1)

    rtl_path = cfg['top_entity']
    if not Path(rtl_path).exists():
        print_err(f"top_entity file not found: '{rtl_path}'")
        sys.exit(1)

    return rtl_path, cfg_path


# ── main pipeline ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Generate AXI4-Lite wrapper for a custom RTL entity.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'Reads {_CONFIG_FILE} from the current directory.',
    )
    parser.add_argument('--no-docs', action='store_true',
                        help='Skip register map Markdown generation')
    args = parser.parse_args()

    rtl_path, cfg_path = _load_config()

    print_banner()
    print_info(f"Config: {bold(_CONFIG_FILE)}")
    print_info(f"Entity file: {bold(rtl_path)}")

    # ── Stage 1A: VHDL parser ──────────────────────────────────────────────
    print_section("Stage 1A — VHDL Entity Parser")
    try:
        from parser.vhdl_parser import parse_entity, ParseError
        entity_name, ports, generics = parse_entity(rtl_path)
        print_ok(f"Entity '{bold(entity_name)}' found in {Path(rtl_path).name}")
        print_info(f"{len(ports)} port(s) extracted")
        if generics:
            print_info(f"{len(generics)} generic(s) found: {', '.join(g.name for g in generics)}")
        for p in ports:
            gd = " [generic-dependent, pass-through]" if p.is_generic_dependent else ""
            print_info(f"  {p.direction.value:6s} {p.name} : {p.vhdl_type} "
                       f"({'?' if p.width is None else f'{p.width}b'}){gd}")
    except Exception as e:
        print_err(f"VHDL parse failed: {e}")
        return 1

    # Output directory: <vhd_dir>/<entity_name>_axi/
    output_dir = Path(rtl_path).parent / f"{entity_name}_axi"

    # ── Stage 1B + 2: JSON validator + IR builder ─────────────────────────
    print_section("Stage 1B + 2 — JSON Validator & IR Builder")
    try:
        from parser.json_validator import build_ir, ValidationError
        ir = build_ir(cfg_path, entity_name, ports, generics)
        print_ok(f"Register map loaded: {len(ir.registers)} register(s)")
        print_ok(f"Data width: {ir.register_width} bits")
        print_ok(f"Address bits required: {ir.addr_bits}")
    except Exception as e:
        for line in str(e).splitlines():
            print_err(line.strip())
        return 1

    # ── Stage 3: Semantic checks ───────────────────────────────────────────
    print_section("Stage 3 — Semantic Checks")
    try:
        from checks.semantic import run_checks, SemanticError
        warnings = run_checks(ir)
        if warnings:
            for w in warnings:
                print_warn(w)
        else:
            print_ok("All semantic checks passed")
    except Exception as e:
        for line in str(e).splitlines():
            print_err(line.strip())
        return 1

    # ── Register map table ─────────────────────────────────────────────────
    print_register_table(ir)

    # ── Stage 4: Code generation ───────────────────────────────────────────
    print_section("Stage 4 — Code Generation")
    try:
        from generator.codegen import generate
        outputs = generate(ir, output_dir, generate_docs=not args.no_docs)
        for label, path in outputs.items():
            size = path.stat().st_size
            print_ok(f"{path.name:40s} {dim(f'({size:,} bytes)')}")
    except Exception as e:
        print_err(f"Code generation failed: {e}")
        traceback.print_exc()
        return 1

    print()
    print(green(bold(f"  Done — outputs written to '{output_dir}/'")) )
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
