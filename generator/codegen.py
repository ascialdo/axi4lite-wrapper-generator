"""
Stage 4 — Code generator.

Renders the Jinja2 templates against the validated IR and writes output files.
"""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ir.model import IR

_TEMPLATE_DIR = Path(__file__).parent.parent / 'templates'

# Collapse the extra blank line Jinja2 inserts between consecutive indented lines.
# Matches: any blank line followed by an indented line (2+ leading spaces).
# Port declarations, signal declarations, port-map entries, and concurrent
# signal assignments all follow this pattern.
_RE_EXTRA_BLANK = re.compile(r'\n\n( {2,}\S)')


def generate(ir: IR, output_dir: str | Path) -> dict[str, Path]:
    """
    Render all templates and write output files.

    Returns a dict { 'axi_lite_if': path, 'top_wrapper': path }.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    # Custom filter: left-justify strings (for port alignment)
    env.filters['ljust'] = lambda s, w: str(s).ljust(w)

    # Custom filter: format integer as zero-padded binary string of given width
    env.filters['bin_addr'] = lambda val, width: format(int(val), f'0{width}b')

    # Post-processor: strip trailing whitespace and the extra blank lines
    # Jinja2 inserts between consecutive conditional/loop-generated lines.
    def _clean(text: str) -> str:
        # Remove trailing whitespace on every line
        text = re.sub(r'[ \t]+\n', '\n', text)
        # Collapse blank line before any indented line (removes extra Jinja2 blanks
        # between port declarations, signal declarations, and port-map entries).
        # Run three times to handle back-to-back occurrences.
        for _ in range(3):
            text = _RE_EXTRA_BLANK.sub(r'\n\1', text)
        # Restore intentional blank lines between major sections
        # (lines starting at column 0, i.e. section comments and keywords)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text

    outputs: dict[str, Path] = {}

    # axi_lite_if.vhd
    tmpl = env.get_template('axi_lite_if.vhd.j2')
    axi_path = output_dir / 'axi_lite_if.vhd'
    axi_path.write_text(_clean(tmpl.render(ir=ir)), encoding='utf-8')
    outputs['axi_lite_if'] = axi_path

    # custom_rtl_axi.vhd
    tmpl = env.get_template('custom_rtl_axi.vhd.j2')
    top_path = output_dir / f'{ir.entity_name}_axi.vhd'
    top_path.write_text(_clean(tmpl.render(ir=ir)), encoding='utf-8')
    outputs['top_wrapper'] = top_path

    return outputs
