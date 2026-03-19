"""
JSON register-map validator and IR builder.

Reads the user-supplied JSON register map, validates it against the schema,
cross-checks port widths against what the VHDL parser resolved, and produces
a validated IR.

Expected JSON format:
{
  "register_width": 32,
  "registers": {
    "REG0": {
      "offset": "0x00",
      "fields": [
        { "port": "prt_en",   "bits": [1, 1], "access": "RW" },
        { "port": "data_bus", "bits": [31, 0], "access": "RO", "readback": "live" }
      ]
    }
  }
}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ir.model import AccessType, Field, IR, Port, Readback, Register


class ValidationError(Exception):
    """Collects multiple validation errors before raising."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(f"  • {e}" for e in errors))


_VALID_ACCESS  = {a.value for a in AccessType}
_VALID_READBACK = {r.value for r in Readback}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_ir(vhdl_path: str | Path,
             json_path:  str | Path,
             entity_name: str,
             ports: list[Port],
             generics: list) -> IR:
    """
    Load + validate the JSON register map, cross-check against parsed ports,
    and return a fully constructed IR.

    Raises ValidationError (with all errors collected) if anything is wrong.
    """
    raw = _load_json(json_path)
    errors: list[str] = []

    register_width = _validate_top_level(raw, errors)
    registers      = _validate_registers(raw, ports, register_width, errors)

    if errors:
        raise ValidationError(errors)

    # Mark ports as mapped
    port_map = {p.name: p for p in ports}
    for reg in registers:
        for fld in reg.fields:
            port_map[fld.port_name].mapped = True

    return IR(
        entity_name=entity_name,
        ports=ports,
        registers=registers,
        register_width=register_width,
        generics=generics,
    )


# ---------------------------------------------------------------------------
# Internal validation helpers
# ---------------------------------------------------------------------------

def _load_json(path: str | Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise ValidationError([f"JSON parse error in {path}: {e}"])
    except FileNotFoundError:
        raise ValidationError([f"Register map file not found: {path}"])


def _validate_top_level(raw: dict, errors: list[str]) -> int:
    rw = raw.get('register_width', 32)
    if rw not in (32, 64):
        errors.append(f"register_width must be 32 or 64, got {rw!r}")
        rw = 32

    if 'registers' not in raw or not isinstance(raw['registers'], dict):
        errors.append("Top-level key 'registers' must be a non-empty object")

    return rw


def _validate_registers(raw: dict,
                         ports: list[Port],
                         register_width: int,
                         errors: list[str]) -> list[Register]:
    port_map = {p.name: p for p in ports}
    registers: list[Register] = []
    seen_offsets: dict[int, str] = {}

    for reg_name, reg_def in raw.get('registers', {}).items():
        if not isinstance(reg_def, dict):
            errors.append(f"{reg_name}: register definition must be an object")
            continue

        # --- offset ---
        offset = _parse_offset(reg_name, reg_def, errors)
        if offset is None:
            continue

        if offset % 4 != 0:
            errors.append(f"{reg_name}: offset {reg_def.get('offset')} is not 4-byte aligned")

        if offset in seen_offsets:
            errors.append(f"{reg_name}: offset {offset:#x} conflicts with {seen_offsets[offset]}")
        else:
            seen_offsets[offset] = reg_name

        # --- fields ---
        fields_raw = reg_def.get('fields', [])
        if not isinstance(fields_raw, list) or not fields_raw:
            errors.append(f"{reg_name}: 'fields' must be a non-empty list")
            continue

        fields, bit_occupation = _validate_fields(
            reg_name, fields_raw, port_map, register_width, errors
        )

        registers.append(Register(name=reg_name, offset=offset, fields=fields))

    # Sort by offset for deterministic output
    registers.sort(key=lambda r: r.offset)
    return registers


def _parse_offset(reg_name: str, reg_def: dict, errors: list[str]):
    raw_offset = reg_def.get('offset')
    if raw_offset is None:
        errors.append(f"{reg_name}: missing 'offset' field")
        return None
    try:
        return int(raw_offset, 16) if isinstance(raw_offset, str) else int(raw_offset)
    except (ValueError, TypeError):
        errors.append(f"{reg_name}: cannot parse offset {raw_offset!r} — use hex string like '0x04'")
        return None


def _validate_fields(reg_name: str,
                      fields_raw: list,
                      port_map: dict[str, Port],
                      register_width: int,
                      errors: list[str]) -> tuple[list[Field], list[int]]:
    fields: list[Field] = []
    bit_used: list[int] = []     # flat list of occupied bit positions

    for i, fdef in enumerate(fields_raw):
        loc = f"{reg_name}.fields[{i}]"

        if not isinstance(fdef, dict):
            errors.append(f"{loc}: field must be an object"); continue

        port_name = fdef.get('port')
        if not port_name:
            errors.append(f"{loc}: missing 'port' key"); continue
        if port_name not in port_map:
            errors.append(f"{loc}: port '{port_name}' not found in VHDL entity"); continue

        port = port_map[port_name]

        # Generic-dependent ports cannot be register-mapped
        if port.is_generic_dependent:
            errors.append(
                f"{loc}: port '{port_name}' has a generic-dependent width "
                f"(type: '{port.vhdl_type}') and cannot be mapped to a register. "
                "It will be passed through as-is. Remove this field from the JSON."
            )
            continue

        bits = fdef.get('bits')
        if not (isinstance(bits, list) and len(bits) == 2):
            errors.append(f"{loc}: 'bits' must be [high, low]"); continue

        bit_high, bit_low = int(bits[0]), int(bits[1])
        if bit_high < bit_low:
            errors.append(f"{loc}: bit_high ({bit_high}) < bit_low ({bit_low})"); continue
        if bit_high >= register_width or bit_low < 0:
            errors.append(f"{loc}: bits [{bit_high}:{bit_low}] out of range for {register_width}-bit register"); continue

        # Bit-width vs port width
        field_width = bit_high - bit_low + 1
        port = port_map[port_name]
        if field_width != port.width:
            errors.append(
                f"{loc}: port '{port_name}' has width {port.width} but bits [{bit_high}:{bit_low}] "
                f"imply width {field_width}"
            )

        # Bit overlap check
        new_bits = list(range(bit_low, bit_high + 1))
        overlap = set(new_bits) & set(bit_used)
        if overlap:
            errors.append(
                f"{loc}: bits {sorted(overlap)} overlap with a previously defined field in {reg_name}"
            )
        bit_used.extend(new_bits)

        # Access type
        access_raw = fdef.get('access', 'RW')
        if access_raw not in _VALID_ACCESS:
            errors.append(f"{loc}: invalid access '{access_raw}', must be one of {sorted(_VALID_ACCESS)}")
            continue
        access = AccessType(access_raw)

        # Readback mode
        readback_raw = fdef.get('readback', 'shadow')
        if readback_raw not in _VALID_READBACK:
            errors.append(f"{loc}: invalid readback '{readback_raw}', must be one of {sorted(_VALID_READBACK)}")
        readback = Readback(readback_raw)

        fields.append(Field(
            port_name=port_name,
            bit_high=bit_high,
            bit_low=bit_low,
            access=access,
            readback=readback,
        ))

    return fields, bit_used
