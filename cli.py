#!/usr/bin/env python3
"""
axi_wrapper — Automated AXI4-Lite wrapper generator for custom RTL.

Standalone mode:
    python cli.py <rtl.vhd> <register_map.json> [--output-dir <dir>]
    axi-wrapper-gen <rtl.vhd> <register_map.json> [--output-dir <dir>]

Integration mode (no positional args — reads axi_wrapper.json in current dir):
    axi-wrapper-gen

Outputs:
    axi_lite_if.vhd       — AXI4-Lite slave register interface
    <entity>_axi.vhd      — Top wrapper connecting DUT to AXI interface
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


# ── config loader (integration mode) ─────────────────────────────────────────

_CONFIG_FILE = 'axi_wrapper.json'

def _load_integration_config() -> tuple[str, str, str]:
    """
    Read axi_wrapper.json from the current directory.
    Returns (rtl_path, regmap_path, output_dir).
    """
    cfg_path = Path(_CONFIG_FILE)
    if not cfg_path.exists():
        print_err(f"Integration mode: '{_CONFIG_FILE}' not found in current directory.")
        print_info("Create it with keys: rtl, regmap, output_dir")
        print_info("Or pass positional args for standalone mode: axi-wrapper-gen <rtl.vhd> <regmap.json>")
        sys.exit(1)

    try:
        cfg = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        print_err(f"Failed to parse '{_CONFIG_FILE}': {e}")
        sys.exit(1)

    missing = [k for k in ('rtl', 'regmap') if k not in cfg]
    if missing:
        print_err(f"'{_CONFIG_FILE}' is missing required keys: {', '.join(missing)}")
        sys.exit(1)

    return cfg['rtl'], cfg['regmap'], cfg.get('output_dir', 'generated')


# ── main pipeline ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Generate AXI4-Lite wrapper for a custom RTL entity.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Integration mode: run with no positional args to read from '
            f'{_CONFIG_FILE} in the current directory.'
        ),
    )
    parser.add_argument('rtl',    nargs='?', help='VHDL source file (.vhd)')
    parser.add_argument('regmap', nargs='?', help='Register map JSON file (.json)')
    parser.add_argument('--output-dir', '-o', default=None,
                        help='Output directory (default: ./output in standalone, ./generated in integration)')
    parser.add_argument('--no-docs', action='store_true',
                        help='Skip register map Markdown generation')
    args = parser.parse_args()

    # Determine mode
    if args.rtl and args.regmap:
        rtl_path    = args.rtl
        regmap_path = args.regmap
        output_dir  = args.output_dir or 'output'
        mode        = 'standalone'
    elif not args.rtl and not args.regmap:
        rtl_path, regmap_path, output_dir = _load_integration_config()
        if args.output_dir:
            output_dir = args.output_dir
        mode = 'integration'
    else:
        print_err("Provide both <rtl> and <regmap>, or neither (integration mode).")
        return 1

    print_banner()
    print_info(f"Mode: {bold(mode)}")

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

    # ── Stage 1B + 2: JSON validator + IR builder ─────────────────────────
    print_section("Stage 1B + 2 — JSON Validator & IR Builder")
    try:
        from parser.json_validator import build_ir, ValidationError
        ir = build_ir(rtl_path, regmap_path, entity_name, ports, generics)
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
    print(green(bold(f"  Done — outputs written to '{output_dir}'/")))
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
