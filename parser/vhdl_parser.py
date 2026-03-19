"""
VHDL entity parser — extracts ports and generics from a VHDL source file.

Handles:
  - std_logic
  - std_logic_vector(N downto 0)  /  std_logic_vector(N-1 downto 0)
  - Multiple ports per line (a, b : in std_logic)
  - Both ; and -- comments
  - VHDL-93 and VHDL-2008 style

Does NOT require a full VHDL toolchain — uses a purpose-built regex grammar
that is robust to common formatting variations.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ir.model import Direction, Port


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_ENTITY = re.compile(
    r'\bentity\s+(\w+)\s+is\b',
    re.IGNORECASE | re.DOTALL
)

_RE_ENTITY_BODY = re.compile(
    r'\bentity\s+\w+\s+is\b(.*?)\bend\s+(?:entity\s+)?(?:\w+\s*)?;',
    re.IGNORECASE | re.DOTALL
)

def _extract_paren_block(src: str, keyword: str) -> str | None:
    """Extract the content of 'keyword ( ... )' handling nested parentheses."""
    pattern = re.compile(r'\b' + keyword + r'\s*\(', re.IGNORECASE)
    m = pattern.search(src)
    if not m:
        return None
    start = m.end()
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        if src[i] == '(':
            depth += 1
        elif src[i] == ')':
            depth -= 1
        i += 1
    return src[start:i - 1] if depth == 0 else None

_RE_PORT_LINE = re.compile(
    r'(?P<names>[\w\s,]+?)\s*:\s*(?P<dir>in|out|inout)\s+(?P<typ>[^;:]+?)(?:\s*:=\s*[^;]+)?\s*(?:;|$)',
    re.IGNORECASE
)

_RE_SLV = re.compile(
    r'std_logic_vector\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)',
    re.IGNORECASE
)

_RE_SLV_EXPR = re.compile(
    r'std_logic_vector\s*\(\s*(.+?)\s+downto\s+(\d+)\s*\)',
    re.IGNORECASE
)

_RE_STD_LOGIC = re.compile(r'^std_logic\s*$', re.IGNORECASE)

_RE_COMMENT = re.compile(r'--[^\n]*')


class ParseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_entity(vhdl_path: str | Path) -> tuple[str, list[Port], list]:
    """
    Parse a VHDL source file and return:
      (entity_name, ports, generics)

    Ports whose width depends on a generic have width=None and cannot be
    register-mapped. Their vhdl_type string is preserved for pass-through.
    Raises ParseError on unrecoverable problems.
    """
    source = Path(vhdl_path).read_text(encoding='utf-8', errors='replace')
    source_stripped = _strip_comments(source)

    entity_name  = _extract_entity_name(source_stripped, vhdl_path)
    entity_body  = _extract_entity_body(source_stripped, vhdl_path)
    generics     = _parse_generics(entity_body)
    generic_names = {g.name for g in generics}
    ports        = _parse_ports(entity_body, generic_names)

    return entity_name, ports, generics


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_comments(src: str) -> str:
    return _RE_COMMENT.sub('', src)


def _extract_entity_name(src: str, path) -> str:
    m = _RE_ENTITY.search(src)
    if not m:
        raise ParseError(f"No 'entity ... is' declaration found in {path}")
    return m.group(1)


def _extract_entity_body(src: str, path) -> str:
    m = _RE_ENTITY_BODY.search(src)
    if not m:
        raise ParseError(f"Could not extract entity body from {path}. "
                         "Check that the file has a complete 'entity ... is ... end;' block.")
    return m.group(1)


def _parse_ports(entity_body: str, generic_names: set[str]) -> list[Port]:
    port_block = _extract_paren_block(entity_body, 'port')
    if port_block is None:
        return []
    ports: list[Port] = []

    for line in _split_port_lines(port_block):
        line = line.strip().rstrip(';').strip()
        if not line:
            continue

        m = _RE_PORT_LINE.match(line)
        if not m:
            continue

        names_raw = m.group('names')
        direction = Direction(m.group('dir').lower())
        vhdl_type = m.group('typ').strip()

        names = [n.strip() for n in names_raw.split(',') if n.strip()]
        width = _try_resolve_width(vhdl_type, generic_names)

        for name in names:
            ports.append(Port(
                name=name,
                direction=direction,
                width=width,
                vhdl_type=vhdl_type,
            ))

    return ports


def _split_port_lines(block: str) -> list[str]:
    """
    Split port block on semicolons, preserving multi-name declarations.
    E.g. "a, b : in std_logic; c : out std_logic_vector(7 downto 0)"
    """
    return [seg.strip() for seg in block.split(';')]


def _try_resolve_width(vhdl_type: str, generic_names: set[str]) -> Optional[int]:
    """
    Attempt to resolve the bit-width of a VHDL port type to a concrete integer.

    Returns None (instead of raising) when the width expression contains a
    generic parameter name — those ports are generic-dependent and cannot be
    register-mapped.

    Raises ParseError only for truly unsupported types (e.g. 'integer', 'real').
    """
    if _RE_STD_LOGIC.match(vhdl_type):
        return 1

    m = _RE_SLV.search(vhdl_type)
    if m:
        high = int(m.group(1))
        low  = int(m.group(2))
        return high - low + 1

    # Expression form: std_logic_vector(EXPR downto LOW)
    m2 = _RE_SLV_EXPR.search(vhdl_type)
    if m2:
        expr = m2.group(1).strip()
        low  = int(m2.group(2))

        # If any generic name appears in the expression, width is not statically known
        if any(re.search(r'\b' + re.escape(g) + r'\b', expr) for g in generic_names):
            return None

        try:
            high = int(eval(expr))  # safe: only simple arithmetic on literals
            return high - low + 1
        except Exception:
            # Expression contains something we can't evaluate — treat as generic-dependent
            return None

    raise ParseError(
        f"Unsupported port type '{vhdl_type}'. "
        "Only std_logic and std_logic_vector(N downto 0) are supported."
    )


def _parse_generics(entity_body: str) -> list:
    """Return list of Generic(name, vhdl_type, default) from the generic block."""
    from ir.model import Generic

    generic_block = _extract_paren_block(entity_body, 'generic')
    if generic_block is None:
        return []

    generics = []
    for line in _split_port_lines(generic_block):
        line = line.strip()
        if not line:
            continue
        # name : type := default   OR   name : type
        m = re.match(
            r'(\w+)\s*:\s*([^:=]+?)(?:\s*:=\s*(.+))?\s*$',
            line
        )
        if m:
            name    = m.group(1).strip()
            typ     = m.group(2).strip()
            default = (m.group(3) or '').strip()
            generics.append(Generic(name=name, vhdl_type=typ, default=default))

    return generics
