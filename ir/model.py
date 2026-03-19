"""
Internal Representation (IR) — single source of truth passed to all pipeline stages.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    IN = "in"
    OUT = "out"
    INOUT = "inout"


class AccessType(str, Enum):
    RW = "RW"   # Read/Write  — register drives output port
    RO = "RO"   # Read-Only   — input port drives register
    WO = "WO"   # Write-Only  — register drives output, reads return 0


class Readback(str, Enum):
    SHADOW = "shadow"  # Read returns last written value (default)
    LIVE   = "live"    # Read returns current port value


@dataclass
class Generic:
    name: str
    vhdl_type: str      # e.g. "integer", "std_logic", "string"
    default: str        # default value as raw VHDL string, e.g. "8", "'0'", "100_000_000"
                        # empty string if no default is specified

    @property
    def declaration(self) -> str:
        """Full VHDL generic declaration line (without trailing semicolon)."""
        if self.default:
            return f"{self.name} : {self.vhdl_type} := {self.default}"
        return f"{self.name} : {self.vhdl_type}"


@dataclass
class Port:
    name: str
    direction: Direction
    width: Optional[int]    # None when width depends on a generic — port cannot be a register
    vhdl_type: str          # original VHDL type string, e.g. "std_logic_vector(DATA_WIDTH-1 downto 0)"
    mapped: bool = False    # set to True once a JSON field references this port

    @property
    def is_generic_dependent(self) -> bool:
        return self.width is None


@dataclass
class Field:
    port_name: str          # references Port.name
    bit_high: int           # MSB position within the register word
    bit_low: int            # LSB position within the register word
    access: AccessType
    readback: Readback = Readback.SHADOW

    @property
    def width(self) -> int:
        return self.bit_high - self.bit_low + 1

    @property
    def mask(self) -> str:
        """Hex mask string for the field within its register."""
        val = ((1 << self.width) - 1) << self.bit_low
        nibbles = (self.width + self.bit_low + 3) // 4
        return f"x\"{val:0{nibbles}X}\""


@dataclass
class Register:
    name: str               # e.g. "REG0"
    offset: int             # byte offset, e.g. 0x00
    fields: list[Field] = field(default_factory=list)

    @property
    def offset_hex(self) -> str:
        return f"0x{self.offset:04X}"

    @property
    def offset_slv(self) -> str:
        """VHDL std_logic_vector literal for address comparison (word address)."""
        word_addr = self.offset >> 2
        return f'"{word_addr:030b}"'


@dataclass
class IR:
    entity_name: str            # original entity name from VHDL
    ports: list[Port]           # all ports from the entity
    registers: list[Register]   # ordered by offset
    register_width: int = 32    # AXI data bus width (32 or 64)
    generics: list['Generic'] = field(default_factory=list)  # DUT generics, forwarded to top wrapper

    def port_by_name(self, name: str) -> Optional[Port]:
        return next((p for p in self.ports if p.name == name), None)

    def unmapped_ports(self) -> list[Port]:
        return [p for p in self.ports if not p.mapped]

    @property
    def addr_bits(self) -> int:
        """Minimum address bits to decode all registers."""
        if not self.registers:
            return 4
        max_offset = max(r.offset for r in self.registers)
        return max(4, (max_offset >> 2).bit_length() + 2)
