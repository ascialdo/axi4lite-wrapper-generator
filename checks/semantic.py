"""
Stage 3 — Semantic checks on the completed IR.

All checks are collected before raising so the user sees every problem at once.
Errors are fatal; warnings are printed but do not abort generation.
"""
from __future__ import annotations

from ir.model import AccessType, Direction, IR


class SemanticError(Exception):
    def __init__(self, errors: list[str], warnings: list[str]):
        self.errors   = errors
        self.warnings = warnings
        lines = []
        if errors:
            lines += [f"  ✗ {e}" for e in errors]
        if warnings:
            lines += [f"  ⚠ {w}" for w in warnings]
        super().__init__("\n".join(lines))


def run_checks(ir: IR) -> list[str]:
    """
    Run all semantic checks.  Returns a (possibly empty) list of warning strings.
    Raises SemanticError if any hard errors are found.
    """
    errors:   list[str] = []
    warnings: list[str] = []

    _check_unmapped_ports(ir, warnings)
    _check_access_vs_direction(ir, warnings)
    _check_register_coverage(ir, warnings)
    _check_addr_space(ir, errors)

    if errors:
        raise SemanticError(errors, warnings)

    return warnings


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_unmapped_ports(ir: IR, warnings: list[str]) -> None:
    unmapped = ir.unmapped_ports()
    # Clock / reset ports are conventionally unmapped — filter common names
    _CLOCK_RESET = {'clk', 'clock', 'rst', 'reset', 'rst_n', 'areset', 'aresetn',
                    's_axi_aclk', 's_axi_aresetn'}
    non_trivial = [p for p in unmapped if p.name.lower() not in _CLOCK_RESET]
    for p in non_trivial:
        warnings.append(
            f"Port '{p.name}' ({p.direction.value} {p.vhdl_type}) is not mapped in the register map — "
            "it will be left unconnected in the top wrapper."
        )


def _check_access_vs_direction(ir: IR, warnings: list[str]) -> None:
    """
    Warn when the JSON access type contradicts the DUT port direction.

    The natural mapping is:
      DUT 'in'  port  <- driven by CPU  -> access should be RW or WO
      DUT 'out' port  -> read by CPU    -> access should be RO

    A mismatch is not a hard error (the wiring will still be generated),
    but it usually indicates a mistake in the register map.
    """
    port_map = {p.name: p for p in ir.ports}
    for reg in ir.registers:
        for fld in reg.fields:
            port = port_map[fld.port_name]
            # DUT output mapped as RW/WO: the register drives back into a DUT
            # output, which is a direction conflict on the DUT side.
            if port.direction == Direction.OUT and fld.access in (AccessType.RW, AccessType.WO):
                warnings.append(
                    f"{reg.name}.{fld.port_name}: DUT port direction is 'out' but access is "
                    f"'{fld.access.value}'. The register will drive a DUT output — "
                    "did you mean 'RO'?"
                )
            # DUT input mapped as RO: the register will capture the DUT input
            # rather than driving it, so CPU writes have no effect.
            if port.direction == Direction.IN and fld.access == AccessType.RO:
                warnings.append(
                    f"{reg.name}.{fld.port_name}: DUT port direction is 'in' but access is 'RO'. "
                    "The register will only capture this signal — CPU writes will be ignored. "
                    "Did you mean 'RW'?"
                )


def _check_register_coverage(ir: IR, warnings: list[str]) -> None:
    """Warn if a register has fewer mapped bits than the bus width."""
    for reg in ir.registers:
        covered = sum(f.width for f in reg.fields)
        if covered < ir.register_width:
            unused = ir.register_width - covered
            warnings.append(
                f"{reg.name}: {unused} of {ir.register_width} bits are unmapped "
                "(they will read as '0' and writes will be ignored)."
            )


def _check_addr_space(ir: IR, errors: list[str]) -> None:
    """Ensure all offsets fit in the AXI address space (max 1 MB, sanity check)."""
    MAX_OFFSET = 0xFFFFC  # 1 MB - 4 bytes
    for reg in ir.registers:
        if reg.offset > MAX_OFFSET:
            errors.append(
                f"{reg.name}: offset {reg.offset_hex} exceeds maximum supported offset {MAX_OFFSET:#x}"
            )
