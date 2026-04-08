"""Config flow for the Gira One integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.instance_id import async_get as async_get_instance_id

from .api import (
    GiraApiAuthError,
    GiraApiClient,
    GiraApiConnectionError,
    GiraApiRequestError,
)
from .const import CLIENT_URN_PREFIX, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _async_validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate the user input allows us to connect and return server info."""
    host = data[CONF_HOST]
    username = data[CONF_USERNAME]
    password = data[CONF_PASSWORD]

    # Generate a unique client ID for this HA instance.
    ha_instance_id = await async_get_instance_id(hass)
    client_id = f"{CLIENT_URN_PREFIX}:{ha_instance_id}"

    api_client = GiraApiClient(host, username, password, hass, auth_error_callback=None)

    # Step 1: Check if the host is a Gira device (unauthenticated)
    await api_client.check_api_availability()

    # Step 2: Register and get a token (authenticated with user/pass)
    token = await api_client.register_client(client_id)

    # Step 3: Fetch detailed server info
    server_info = await api_client.get_server_details()

    # Step 4: Build a friendly title, including the host for clarity.
    device_name = server_info.get("deviceName")
    if device_name and device_name.lower() != host.lower():
        title = f"{device_name} ({host})"
    else:
        title = host

    # Prefer a stable device identifier over the host for the unique_id.
    # Fall back to the host if none is advertised by the server.
    unique_id = (
        server_info.get("deviceId")
        or server_info.get("serialNumber")
        or server_info.get("uid")
        or host
    )

    return {
        "title": title,
        "unique_id": str(unique_id),
        "client_id": client_id,
        "access_token": token,
        "server_info": server_info,
    }


class GiraOneConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gira One."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry_data: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                validated = await _async_validate_input(self.hass, user_input)
            except GiraApiAuthError:
                _LOGGER.debug("Authentication failed during setup")
                errors["base"] = "invalid_auth"
            except GiraApiConnectionError:
                _LOGGER.debug("Connection error during setup")
                errors["base"] = "cannot_connect"
            except GiraApiRequestError as err:
                _LOGGER.debug("API request error during setup: %s", err)
                if "locked" in str(err).lower():
                    errors["base"] = "device_locked"
                else:
                    errors["base"] = "api_error"
            except Exception:
                _LOGGER.exception("Unexpected exception during Gira One setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(validated["unique_id"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST]}
                )
                return self.async_create_entry(
                    title=validated["title"],
                    data={
                        **user_input,
                        "client_id": validated["client_id"],
                        "access_token": validated["access_token"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a reauthentication flow when credentials become invalid."""
        self._reauth_entry_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication with new credentials."""
        assert self._reauth_entry_data is not None
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            combined = {
                CONF_HOST: self._reauth_entry_data[CONF_HOST],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                validated = await _async_validate_input(self.hass, combined)
            except GiraApiAuthError:
                errors["base"] = "invalid_auth"
            except GiraApiConnectionError:
                errors["base"] = "cannot_connect"
            except GiraApiRequestError as err:
                if "locked" in str(err).lower():
                    errors["base"] = "device_locked"
                else:
                    errors["base"] = "api_error"
            except Exception:
                _LOGGER.exception("Unexpected exception during Gira One reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "client_id": validated["client_id"],
                        "access_token": validated["access_token"],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"host": self._reauth_entry_data[CONF_HOST]},
        )
