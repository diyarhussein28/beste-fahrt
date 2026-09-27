from shared.config import PlatformEntry
from collector.movacarpro_parser import MovacarProParser


def make_parser() -> MovacarProParser:
    return MovacarProParser(PlatformEntry(name="movacarpro"))


def test_parse_card_text_basic_ride():
    parser = make_parser()
    text = "Bronze Weiden → Saal a. d. Donau Fr 25.09.2026 12:48 → Di 29.09.2026 16:00 56,00 €"
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


def test_parse_card_text_with_requirement_tag():
    parser = make_parser()
    text = "Bronze Kassel → Lohfelden Fr 02.10.2026 13:00 → Fr 02.10.2026 15:00 Transport auf Anhänger 42,00 €"
    offer = parser._parse_card_text(text)
    assert offer is not None
    assert offer.required_license == "anhaenger"


def test_parse_card_text_with_thousand_separator_price():
    parser = make_parser()
    text = "Gold Bochum → Niederaula Mi 30.09.2026 10:30 → Mi 30.09.2026 12:00 1.132,00 €"
    offer = parser._parse_card_text(text)
    assert offer is not None
    assert offer.price_eur == 1132.00


def test_parse_card_text_missing_price_returns_none():
    parser = make_parser()
    assert parser._parse_card_text("Bronze Weiden → Saal a. d. Donau Fr 25.09.2026 12:48") is None


def test_parse_card_text_missing_route_arrow_returns_none():
    parser = make_parser()
    assert parser._parse_card_text("Bronze Weiden Saal a. d. Donau 56,00 €") is None
