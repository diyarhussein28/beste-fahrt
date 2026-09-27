from shared.config import PlatformEntry
from collector.movacarpro_parser import MovacarProParser


def make_parser() -> MovacarProParser:
    return MovacarProParser(PlatformEntry(name="movacarpro"))


def test_parse_card_text_basic_ride():
    # Confirmed against the live site: price sits between the tier badge and
    # the route, and the card the extraction JS grabs also carries the date.
    parser = make_parser()
    text = "Bronze 56,00 € Weiden → Saal a. d. Donau Fr 25.09.2026 12:48 → Di 29.09.2026 16:00"
    offer = parser._parse_card_text(text)
    assert offer is not None
    assert offer.pickup_address == "Weiden"
    assert offer.dropoff_address == "Saal a. d. Donau"
    assert offer.price_eur == 56.00
    assert offer.pickup_date.year == 2026
    assert offer.pickup_date.month == 9
    assert offer.pickup_date.day == 25
    assert offer.pickup_date.hour == 12
    assert offer.pickup_date.minute == 48
    assert offer.platform == "movacarpro"
    assert offer.required_license is None
    assert offer.url is None  # no href given


def test_parse_card_text_resolves_relative_href_against_root():
    parser = make_parser()
    text = "Bronze 56,00 € Weiden → Saal a. d. Donau Fr 25.09.2026 12:48 → Di 29.09.2026 16:00"
    offer = parser._parse_card_text(text, href="/fahrten/12345")
    assert offer is not None
    assert offer.url == "https://movacarpro.com/fahrten/12345"


def test_parse_card_text_keeps_absolute_href_as_is():
    parser = make_parser()
    text = "Bronze 56,00 € Weiden → Saal a. d. Donau Fr 25.09.2026 12:48 → Di 29.09.2026 16:00"
    offer = parser._parse_card_text(text, href="https://movacarpro.com/fahrten/999")
    assert offer is not None
    assert offer.url == "https://movacarpro.com/fahrten/999"


def test_parse_card_text_without_tier_badge():
    # Some live cards had no visible tier word at all.
    parser = make_parser()
    text = "53,00 € Warendorf → Osnabrück Di 29.09.2026 09:00 → Di 29.09.2026 12:00"
    offer = parser._parse_card_text(text)
    assert offer is not None
    assert offer.pickup_address == "Warendorf"
    assert offer.dropoff_address == "Osnabrück"


def test_parse_card_text_with_requirement_tag():
    parser = make_parser()
    text = "Bronze 42,00 € Kassel → Lohfelden Fr 02.10.2026 13:00 → Fr 02.10.2026 15:00 Transport auf Anhänger"
    offer = parser._parse_card_text(text)
    assert offer is not None
    assert offer.required_license == "anhaenger"


def test_parse_card_text_with_thousand_separator_price():
    parser = make_parser()
    text = "Gold 1.132,00 € Bochum → Niederaula Mi 30.09.2026 10:30 → Mi 30.09.2026 12:00"
    offer = parser._parse_card_text(text)
    assert offer is not None
    assert offer.price_eur == 1132.00


def test_parse_card_text_missing_price_returns_none():
    parser = make_parser()
    assert parser._parse_card_text("Bronze Weiden → Saal a. d. Donau Fr 25.09.2026 12:48") is None


def test_parse_card_text_missing_route_arrow_returns_none():
    parser = make_parser()
    assert parser._parse_card_text("Bronze 56,00 € Weiden Saal a. d. Donau") is None


def test_parse_card_text_without_date_still_parses_route_and_price():
    # No date match -> route_end falls back to end of string; still usable.
    parser = make_parser()
    text = "Bronze 56,00 € Weiden → Saal a. d. Donau"
    offer = parser._parse_card_text(text)
    assert offer is not None
    assert offer.pickup_address == "Weiden"
    assert offer.dropoff_address == "Saal a. d. Donau"
    assert offer.pickup_date is None
