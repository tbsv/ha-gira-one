"""Config flow for the Gira One integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
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


async def _async_validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """
    Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    host = data[CONF_HOST]
    username = data[CONF_USERNAME]
    password = data[CONF_PASSWORD]

    # Generate a truly unique client ID for this HA instance.
    ha_instance_id = await async_get_instance_id(hass)
    client_id = f"{CLIENT_URN_PREFIX}:{ha_instance_id}"

    api_client = GiraApiClient(
        host, username, password, hass, auth_error_callback=None
    )

    # Step 1: Check if the host is a Gira device (unauthenticated)
    await api_client.check_api_availability()

    # Step 2: Register and get a token (authenticated with user/pass)
    token = await api_client.register_client(client_id)

    # Step 3: Now that we are authenticated, get the detailed server info
    server_info = await api_client.get_server_details()

    # Step 4: Construct a friendly title, ensuring the host is always included for clarity.
    device_name = server_info.get("deviceName")
    if device_name and device_name.lower() != host.lower():
        # If a device name exists and it's different from the host, combine them.
        title = f"{device_name} ({host})"
    else:
        # Otherwise, just use the host.
        title = host

    return {
        "title": title,
        "client_id": client_id,
        "access_token": token,
    }


class GiraOneConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gira One."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    async def async_step_user(self, user_input: dict | None = None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                # Use the validation helper function
                validated_info = await _async_validate_input(self.hass, user_input)

                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=validated_info["title"],
                    data={
                        **user_input,  # Store host, username, password
                        "client_id": validated_info["client_id"],
                        "access_token": validated_info["access_token"],
                    },
                )

            except GiraApiAuthError:
                _LOGGER.exception("Authentication failed during setup")
                errors["base"] = "invalid_auth"
            except GiraApiConnectionError:
                _LOGGER.exception("Connection error during setup")
                errors["base"] = "cannot_connect"
            except GiraApiRequestError as e:
                _LOGGER.exception("API request error during setup: %s", e)
                if "locked" in str(e).lower():
                    errors["base"] = "device_locked"
                else:
                    errors["base"] = "api_error"
            except Exception:  # pylint: disable=broad-except-clause
                _LOGGER.exception("Unexpected exception during Gira IoT setup")
                errors["base"] = "unknown"

        # Show the form to the user
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )