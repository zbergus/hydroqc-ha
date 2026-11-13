"""Unit tests for the hourly consumption fetch error classification.

Hydro-Québec answers 400 on resourceObtenirDonneesConsommationHoraires while the
hourly buckets of a day are not published yet. That is publish lag, not a fault,
so it must not be logged as an error - a 5xx on that endpoint still is one.
"""

import datetime
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from hydroqc.error import HydroQcHTTPError

from custom_components.hydroqc.statistics_manager import StatisticsManager

DAY = datetime.date(2026, 11, 12)
LOGGER_NAME = "custom_components.hydroqc.statistics_manager"
HOURLY_CONSUMPTION_URL = (
    "https://services-cl.solutions.hydroquebec.com/lsw/portail/fr/group/clientele/"
    "portrait-de-consommation/resourceObtenirDonneesConsommationHoraires"
)


def _manager(hass: HomeAssistant, error: Exception) -> StatisticsManager:
    """Return a StatisticsManager whose hourly fetch raises ``error``."""
    contract = MagicMock()
    contract.get_hourly_consumption = AsyncMock(side_effect=error)
    return StatisticsManager(
        hass=hass,
        contract=contract,
        rate="D",
        get_statistic_id_func=lambda consumption_type: f"hydroqc:home_{consumption_type}_hourly",
        contract_name="home",
    )


def _http_error(status_code: int) -> HydroQcHTTPError:
    """Return the error the hydroqc library raises for a non-200 answer."""
    return HydroQcHTTPError(f"Error Fetching {HOURLY_CONSUMPTION_URL} - {status_code}", status_code)


def _records_at(caplog: pytest.LogCaptureFixture, level: int) -> list[Any]:
    """Return the captured records logged at exactly ``level``."""
    return [record for record in caplog.records if record.levelno == level]


def _warnings_and_errors(caplog: pytest.LogCaptureFixture) -> list[Any]:
    """Return the captured records logged at WARNING or above."""
    return [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("status_code", [400, 404])
async def test_not_published_status_is_not_an_error(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    status_code: int,
) -> None:
    """A 4xx meaning "not published yet" is logged once at WARNING, never ERROR."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    await _manager(hass, _http_error(status_code)).fetch_and_import_hourly_consumption(DAY, DAY)

    assert _records_at(caplog, logging.ERROR) == []
    warnings = _records_at(caplog, logging.WARNING)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "not yet published by HQ" in message
    assert str(DAY) in message
    assert str(status_code) in message


async def test_server_error_is_still_logged_as_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A 5xx on the hourly endpoint is a real outage and stays at ERROR."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    await _manager(hass, _http_error(500)).fetch_and_import_hourly_consumption(DAY, DAY)

    errors = _records_at(caplog, logging.ERROR)
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "HTTP error fetching consumption" in message
    assert str(DAY) in message
    assert "500" in message
    assert _records_at(caplog, logging.WARNING) == []


async def test_auth_status_is_still_logged_as_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A 401/403 is an authentication fault, not publish lag: keep it at ERROR."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    await _manager(hass, _http_error(403)).fetch_and_import_hourly_consumption(DAY, DAY)

    errors = _records_at(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "403" in errors[0].getMessage()
    assert _warnings_and_errors(caplog) == errors


async def test_parse_failure_is_still_logged_as_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A transport/parse failure carries no status code and stays at ERROR."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    await _manager(
        hass, HydroQcHTTPError("Bad JSON format at line 1 column 2: Expecting value")
    ).fetch_and_import_hourly_consumption(DAY, DAY)

    errors = _records_at(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "Bad JSON format" in errors[0].getMessage()
    assert _warnings_and_errors(caplog) == errors


async def test_empty_response_stays_at_debug(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The library's "no data for that date" sentinel keeps its DEBUG line."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    await _manager(
        hass, HydroQcHTTPError(f"No data available for date {DAY.isoformat()}")
    ).fetch_and_import_hourly_consumption(DAY, DAY)

    assert _warnings_and_errors(caplog) == []
    debugs = [record.getMessage() for record in _records_at(caplog, logging.DEBUG)]
    assert any("no consumption data available" in message.lower() for message in debugs)
