"""Config flow for the Gira One integration."""
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME


from .api import (
    GiraApiAuthError,
    GiraApiClient,
    GiraApiConnectionError,
    GiraApiRequestError,
)
from .const import CLIENT_URN_PREFIX, DOMAIN

_LOGGER = logging.getLogger(__name__)


class GiraIoTConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gira One."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH # Callbacks make it push

    async def async_step_user(self, user_input: dict | None = None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Generate a unique client ID for this HA instance [cite: 40]
            # Example URN: de.gira.gdsrestapi.clients.my_well_known_service [cite: 40]
            instance_id = "test"
            client_id = f"{CLIENT_URN_PREFIX}:{instance_id}"


            try:
                api_client = GiraApiClient(host, username, password, self.hass)
                await api_client.check_api_availability() # Check before trying to register
                token = await api_client.register_client(client_id) # [cite: 44]

                if token:
                    await self.async_set_unique_id(host) # Assuming host is unique identifier for the device
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=host, # Or use deviceName from availability check
                        data={
                            CONF_HOST: host,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password, # Storing password, consider alternatives if sensitive
                            "client_id": client_id, # Store generated client_id
                            "access_token": token # Store initial token
                        },
                    )
                else:
                    errors["base"] = "registration_failed" # Should be caught by exceptions below

            except GiraApiAuthError: # [cite: 45]
                _LOGGER.error("Authentication failed during setup")
                errors["base"] = "invalid_auth"
            except GiraApiConnectionError:
                _LOGGER.error("Connection error during setup")
                errors["base"] = "cannot_connect"
            except GiraApiRequestError as e: # Includes 423 Locked [cite: 45]
                _LOGGER.error("API request error during setup: %s", e)
                if "locked" in str(e).lower(): # Based on error code "locked" [cite: 25, 45]
                    errors["base"] = "device_locked"
                else:
                    errors["base"] = "api_error"
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during Gira IoT setup: %s", e)
                errors["base"] = "unknown"

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

    # TODO: Implement options flow if needed later for things like polling interval (if callbacks fail)
    # @staticmethod
    # @callback
    # def async_get_options_flow(config_entry):
    #     """Get the options flow for this handler."""
    #     return GiraOptionsFlowHandler(config_entry)

# class GiraOptionsFlowHandler(config_entries.OptionsFlow):
# ...