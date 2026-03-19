#!/usr/bin/env python3
"""
axi_wrapper — Automated AXI4-Lite wrapper generator for custom RTL.

Usage:
    python cli.py <rtl.vhd> <register_map.json> [--output-dir <dir>]

Outputs:
    axi_lite_if.vhd       — AXI4-Lite slave register interface
    <entity>_axi.vhd      — Top wrapper connecting DUT to AXI interface
"""
from __future__ import annotations

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


# ── main pipeline ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Generate AXI4-Lite wrapper for a custom RTL entity.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('rtl',      help='VHDL source file (.vhd)')
    parser.add_argument('regmap',   help='Register map JSON file (.json)')
    parser.add_argument('--output-dir', '-o', default='output',
                        help='Output directory (default: ./output)')
    args = parser.parse_args()

    print_banner()

    # ── Stage 1A: VHDL parser ──────────────────────────────────────────────
    print_section("Stage 1A — VHDL Entity Parser")
    try:
        from parser.vhdl_parser import parse_entity, ParseError
        entity_name, ports, generics = parse_entity(args.rtl)
        print_ok(f"Entity '{bold(entity_name)}' found in {Path(args.rtl).name}")
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
        ir = build_ir(args.rtl, args.regmap, entity_name, ports, generics)
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
        outputs = generate(ir, args.output_dir)
        for label, path in outputs.items():
            size = path.stat().st_size
            print_ok(f"{'axi_lite_if.vhd' if label == 'axi_lite_if' else path.name:40s} "
                     f"{dim(f'({size:,} bytes)')}")
    except Exception as e:
        print_err(f"Code generation failed: {e}")
        traceback.print_exc()
        return 1

    print()
    print(green(bold(f"  Done — outputs written to '{args.output_dir}'/")) )
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
