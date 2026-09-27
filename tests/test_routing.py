from matcher.routing import haversine_km


def test_haversine_zero_distance():
    assert haversine_km(51.0459, 7.0192, 51.0459, 7.0192) == 0.0


def test_haversine_leverkusen_to_cologne():
    # Leverkusen -> Köln is roughly 10-15 km as the crow flies.
    km = haversine_km(51.0459, 7.0192, 50.9375, 6.9603)
    assert 8 < km < 18


def test_haversine_symmetric():
    a = haversine_km(51.0, 7.0, 48.13, 11.58)
    b = haversine_km(48.13, 11.58, 51.0, 7.0)
    assert a == b
