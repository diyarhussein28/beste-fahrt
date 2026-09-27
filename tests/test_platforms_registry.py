from shared.config import PlatformEntry
from collector.platforms import build_parser
from collector.movacarpro_parser import MovacarProParser
from collector.example_parser import ConfigDrivenParser


def test_build_parser_resolves_movacarpro():
    entry = PlatformEntry(name="movacarpro")
    parser = build_parser(entry)
    assert isinstance(parser, MovacarProParser)
    assert parser.name == "movacarpro"


def test_build_parser_resolves_generic_by_explicit_key():
    entry = PlatformEntry(name="some_new_account", parser="generic")
    parser = build_parser(entry)
    assert isinstance(parser, ConfigDrivenParser)
    assert parser.name == "some_new_account"


def test_build_parser_returns_none_for_unknown_key():
    entry = PlatformEntry(name="totally_unregistered_platform")
    assert build_parser(entry) is None
