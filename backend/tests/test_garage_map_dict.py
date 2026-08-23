"""Contract tests for ``app.services.matching.garage_to_map_dict``.

The frontend ProviderList renders ``garage.services`` from the map dict;
the key must always be present and must serialize as ``list[str]``
(never ``None``/undefined), even when the source value is empty, None,
or a JSON text string (SQLite ``text()`` rows bypass ARRAY processors).
"""

from uuid import uuid4

from app.services.matching import RankedGarage, garage_to_map_dict


def _garage(services) -> RankedGarage:
    return RankedGarage(
        id=uuid4(),
        garage_name="Test Garage",
        lat=28.61,
        lon=77.21,
        rating=4.5,
        distance_m=1200.0,
        score=0.9,
        services=services,
    )


def test_services_present_in_map_dict() -> None:
    """Populated services list round-trips into the map dict."""
    d = garage_to_map_dict(_garage(["towing", "engine"]))

    assert d["services"] == ["towing", "engine"]


def test_empty_services_serialize_as_list_not_none() -> None:
    """Empty services → [] (never None/undefined)."""
    d = garage_to_map_dict(_garage([]))

    assert d["services"] == []


def test_none_services_coerced_to_empty_list() -> None:
    """Dataclasses don't enforce types; serializer must still guarantee []."""
    d = garage_to_map_dict(_garage(None))

    assert d["services"] == []


def test_json_string_services_parsed_to_list() -> None:
    """SQLite text() rows deliver ARRAY(String) as JSON text — parse it."""
    d = garage_to_map_dict(_garage('["towing"]'))

    assert d["services"] == ["towing"]


def test_existing_map_dict_shape_unchanged() -> None:
    """Regression guard: pre-existing keys keep their values."""
    g = _garage(["towing"])
    d = garage_to_map_dict(g)

    assert d["id"] == str(g.id)
    assert d["name"] == "Test Garage"
    assert d["location"] == [28.61, 77.21]
    assert d["rating"] == 4.5
    assert d["distanceKm"] == 1.2
