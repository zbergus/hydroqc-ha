"""Runtime fixes for the Hydro-Quebec-API-Wrapper library.

These patches work around bugs in the pinned library version until they are
fixed upstream (https://gitlab.com/hydroqc/hydroqc). Each patch is idempotent.

Design notes (aligned with hydroqc.ca / library docs):
- OAuth tokens and the portal web session are intentionally independent. The
  upstream ``_create_web_session`` clears cookies, refreshes the access token,
  then calls ``maj-session`` with Bearer headers (see library comments and the
  Dec 2023 "independent OAuth / web session" change).
- ``maj-session`` is a ``/portail/`` URL; upstream tracks "avoid all the
  /portail/ URLs" as a TODO on Read the Docs. We do not invent a new session
  protocol here — we only add a single re-login retry on HTTP 403.
- Portal maintenance / intermittent HTTP errors are expected per
  https://hydroqc.ca/en/docs/troubleshooting/ — HA-side cooldown belongs in
  the coordinator, not in a more aggressive library retry loop.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from hydroqc.error import HydroQcHTTPError
from hydroqc.hydro_api.client import HydroClient
from hydroqc.hydro_api.consts import SESSION_URL

_LOGGER = logging.getLogger(__name__)

_PATCHED_ATTR = "_hydroqc_ha_patched"


def _get_token_data_base64url(self: HydroClient) -> Any:
    """Decode the id token payload as base64url, per the JWT spec (RFC 7519).

    The library uses ``base64.b64decode``, which silently drops the base64url
    characters ``-`` and ``_``. Payloads that contain them (for example, a name
    with an accent such as "Noémie") then fail with ``binascii.Error: Incorrect
    padding`` right after a successful login.

    Upstream known-issue workaround is to rename the account holder; decoding
    as base64url is the correct JWT fix (see also hydroqc-ha PR #145).
    """
    if not self._id_token:
        return None
    raw_token_data = self._id_token.split(".")[1]
    raw_token_data += "=" * ((4 - len(raw_token_data) % 4) % 4)
    return self._load_json(base64.urlsafe_b64decode(raw_token_data))


async def _create_web_session_resilient(
    self: HydroClient, applicant_id: str, customer_id: str, contract_id: str
) -> None:
    """Create a portal web session using upstream semantics, with one 403 retry.

    Preserves upstream behavior:
    1. Clear cookies (new portal session must be created from the access token)
    2. Force-refresh the OAuth access token
    3. GET ``maj-session/?mode=web`` with customer Bearer headers

    On HTTP 403 only: close session, full ``login()``, and repeat once. Further
    backoff is handled by the HA coordinator so we do not hammer the portal
    during maintenance windows (see hydroqc.ca troubleshooting).
    """
    self._logger.debug("Creating new web session %s", contract_id)

    async def _attempt() -> None:
        # Clearing all cookies. New SESSION must be created.
        try:
            self._cookie_jar.clear()
        except AttributeError:  # pragma: no cover - defensive
            pass

        # Forcing a refresh of the access token.
        # The new session that is created with SESSION_URL (/portal resources)
        # seemed to break (calls returning HTTP 400) in alignment with
        # the access_token expiry. With a new access_token, we can assume the
        # web session invalid at the same time as the access token that was
        # used to create it.
        headers = await self._get_customer_http_headers(
            applicant_id, customer_id, force_refresh=True
        )
        params = {"mode": "web"}
        await self.http_request(SESSION_URL, "get", params=params, headers=headers)
        self.web_session_expiry = self.access_token_expiry

    try:
        await _attempt()
        return
    except HydroQcHTTPError as err:
        if err.status_code != 403:
            raise
        self._logger.warning(
            "maj-session returned 403; performing one full re-login and retry"
        )

    await self.close_session()
    await self.login()
    await _attempt()


def apply_patches() -> None:
    """Apply all library patches once."""
    if getattr(HydroClient, _PATCHED_ATTR, False):
        return
    HydroClient.get_token_data = _get_token_data_base64url  # type: ignore[method-assign]
    HydroClient._create_web_session = _create_web_session_resilient  # type: ignore[method-assign]
    setattr(HydroClient, _PATCHED_ATTR, True)
    _LOGGER.debug(
        "Applied HydroClient patches: base64url id token + maj-session 403 retry"
    )
