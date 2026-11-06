"""Tests for the portal HTTP 403 cooldown (Hydro-Québec overnight maintenance)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import hydroqc.error
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc.coordinator import HydroQcDataCoordinator

TZ = ZoneInfo("America/Toronto")


def _coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    webuser: MagicMock,
    public_client: MagicMock,
) -> HydroQcDataCoordinator:
    entry.add_to_hass(hass)
    with (
        patch("custom_components.hydroqc.coordinator.base.WebUser", return_value=webuser),
        patch(
            "custom_components.hydroqc.coordinator.base.PublicDataClient",
            return_value=public_client,
        ),
    ):
        return HydroQcDataCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_403_starts_cooldown_and_does_not_fail_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webuser: MagicMock,
    mock_public_client: MagicMock,
) -> None:
    """A 403 soft-fails (no UpdateFailed) so setup does not retry-storm the portal."""
    mock_webuser.get_info.side_effect = hydroqc.error.HydroQcHTTPError("maj-session - 403", 403)
    coordinator = _coordinator(hass, mock_config_entry, mock_webuser, mock_public_client)

    data = await coordinator._async_update_data()

    assert data["contract"] is None
    assert coordinator._portal_cooldown_until is not None
    assert coordinator._portal_cooldown_until > datetime.now(TZ) + timedelta(minutes=59)


@pytest.mark.asyncio
async def test_cooldown_skips_portal_calls(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webuser: MagicMock,
    mock_public_client: MagicMock,
) -> None:
    """While cooling down, no status check, login or fetch reaches the portal."""
    coordinator = _coordinator(hass, mock_config_entry, mock_webuser, mock_public_client)
    coordinator._portal_cooldown_until = datetime.now(TZ) + timedelta(minutes=30)

    await coordinator._async_update_data()

    mock_webuser.check_hq_portal_status.assert_not_awaited()
    mock_webuser.get_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_cooldown_retries_and_resets_on_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webuser: MagicMock,
    mock_public_client: MagicMock,
) -> None:
    """After the cooldown, the portal is queried again and success clears the state."""
    coordinator = _coordinator(hass, mock_config_entry, mock_webuser, mock_public_client)
    coordinator._portal_cooldown_until = datetime.now(TZ) - timedelta(seconds=1)

    await coordinator._async_update_data()

    mock_webuser.get_info.assert_awaited()
    assert coordinator._portal_cooldown_until is None


@pytest.mark.asyncio
async def test_non_403_http_error_still_fails_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webuser: MagicMock,
    mock_public_client: MagicMock,
) -> None:
    """Only 403 is soft-failed; other HTTP errors keep upstream behaviour."""
    mock_webuser.get_info.side_effect = hydroqc.error.HydroQcHTTPError("boom - 500", 500)
    coordinator = _coordinator(hass, mock_config_entry, mock_webuser, mock_public_client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator._portal_cooldown_until is None
