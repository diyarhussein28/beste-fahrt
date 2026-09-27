"""Registry mapping a config.yaml platform entry's `parser` key (or its
`name`, when the two match) to an `OfferParser` implementation. Add a new
platform by writing a parser module and registering it here — no other
file needs to change to support another platform.
"""
from __future__ import annotations

from shared.config import PlatformEntry
from collector.example_parser import ConfigDrivenParser
from collector.movacarpro_parser import MovacarProParser
from collector.parser import OfferParser

REGISTRY: dict[str, type[OfferParser]] = {
    "generic": ConfigDrivenParser,
    "movacarpro": MovacarProParser,
}


def build_parser(entry: PlatformEntry) -> OfferParser | None:
    parser_cls = REGISTRY.get(entry.parser_key())
    if parser_cls is None:
        return None
    return parser_cls(entry)
