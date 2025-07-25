"""Gira One integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import get_url, NoURLAvailableError
from homeassistant.components.http import HomeAssistantView

from .api import (
    GiraApiAuthError,
    GiraApiClient,
    GiraApiConnectionError,
    GiraApiRequestError,
    GiraApiClientError,
)
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    EVENT_TYPE_TEST,
    EVENT_TYPE_PROJECT_CONFIG_CHANGED,
    EVENT_TYPE_UI_CONFIG_CHANGED,
    EVENT_TYPE_RESTART,
    EVENT_TYPE_STARTUP,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_CALLBACK_PATH = f"/api/{DOMAIN}/service_callback"
VALUE_CALLBACK_PATH = f"/api/{DOMAIN}/value_callback"

# Used to signal entities that new data is available
SIGNAL_DATA_UPDATE = f"{DOMAIN}_data_update"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gira One from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data["client_id"]
    access_token = entry.data["access_token"]

    api_client = GiraApiClient(host, username, password, hass)
    api_client.set_credentials(token=access_token, client_id=client_id)

    try:
        # Verify token is still valid by fetching UI config
        _LOGGER.debug("Attempting to fetch UI config with stored token.")
        ui_config = await api_client.get_ui_config()
    except GiraApiAuthError:
        _LOGGER.warning(
            "Initial token seems invalid or expired, attempting to re-register client."
        )
        try:
            new_token = await api_client.register_client(client_id)
            new_data = {**entry.data, "access_token": new_token}
            hass.config_entries.async_update_entry(entry, data=new_data)
            api_client.set_credentials(token=new_token, client_id=client_id)
            _LOGGER.info("Client re-registered successfully, new token stored.")
            ui_config = await api_client.get_ui_config()  # Retry with new token
        except (GiraApiAuthError, GiraApiRequestError) as e:
            _LOGGER.error("Failed to re-register client: %s", e)
            return False
    except (GiraApiConnectionError, GiraApiRequestError) as e:
        _LOGGER.error("Failed to connect or communicate with Gira One Server: %s", e)
        return False  # Defer setup, HA will retry

    _LOGGER.info("Successfully connected to Gira IoT API and fetched UI config.")

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api_client,
        DATA_UI_CONFIG: ui_config,
    }

    # Register device
    await _async_register_device(hass, entry, api_client)

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register callback views
    if not await _async_register_callbacks(hass, entry, api_client):
        # If callbacks are essential and fail, we might want to fail the setup.
        return False

    # Register cleanup hooks
    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            lambda event: _async_cleanup_resources(hass, entry),
        )
    )
    entry.async_on_unload(lambda: _async_cleanup_resources(hass, entry))

    return True


async def _async_register_device(
    hass: HomeAssistant, entry: ConfigEntry, api_client: GiraApiClient
) -> None:
    """Register the main Gira One device in the device registry."""
    try:
        server_details = await api_client.get_server_details()
    except GiraApiClientError as e:
        _LOGGER.warning("Could not fetch detailed server info: %s", e)
        server_details = {}

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id or entry.data[CONF_HOST])},
        name=entry.title,
        manufacturer="Gira",
        model=server_details.get("deviceType", "Gira One Server"),
        sw_version=server_details.get("deviceVersion"),
    )


async def _async_register_callbacks(
    hass: HomeAssistant, entry: ConfigEntry, api_client: GiraApiClient
) -> bool:
    """Register callback URLs and views with Home Assistant and Gira device."""
    try:
        base_url = get_url(
            hass, require_ssl=True, allow_internal=False, prefer_external=True
        )
    except NoURLAvailableError:
        _LOGGER.error(
            "Cannot determine external SSL URL for Gira callbacks. "
            "Please configure Home Assistant's external_url with SSL."
        )
        return False

    service_callback_url = f"{base_url}{SERVICE_CALLBACK_PATH}"
    value_callback_url = f"{base_url}{VALUE_CALLBACK_PATH}"

    _LOGGER.info("Registering Gira Service Callback URL: %s", service_callback_url)
    _LOGGER.info("Registering Gira Value Callback URL: %s", value_callback_url)

    hass.http.register_view(GiraServiceCallbackView(hass, entry.entry_id))
    hass.http.register_view(GiraValueCallbackView(hass, entry.entry_id))

    try:
        await api_client.register_callbacks(service_callback_url, value_callback_url)
        _LOGGER.info("Gira callbacks registered successfully.")
    except GiraApiClientError as e:
        _LOGGER.error("Failed to register Gira callbacks: %s", e)
        return False
    return True


async def _async_cleanup_resources(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Centralized function to clean up resources, unregister client and callbacks.
    """
    api_client: GiraApiClient = hass.data[DOMAIN][entry.entry_id].get(DATA_API_CLIENT)
    if not api_client:
        return

    _LOGGER.info("Cleaning up Gira One resources for %s", entry.title)
    try:
        await api_client.remove_callbacks()
    except GiraApiClientError as e:
        _LOGGER.error("Error removing Gira callbacks: %s", e)
    try:
        await api_client.unregister_client()
    except GiraApiClientError as e:
        _LOGGER.error("Error unregistering Gira client: %s", e)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Gira One entry: %s", entry.title)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("Gira One entry %s unloaded successfully.", entry.title)

    return unload_ok


class BaseGiraCallbackView(HomeAssistantView):
    """Base View for Gira Callbacks."""

    requires_auth = False
    url = "OVERRIDE_IN_SUBCLASS"
    name = "OVERRIDE_IN_SUBCLASS_WITH_API_PREFIX"
    cors_allowed = True

    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self.entry_id = entry_id

    async def post(self, request):
        """Handle POST requests from Gira API."""
        try:
            data = await request.json()
        except ValueError:
            _LOGGER.warning("Received invalid JSON in Gira callback %s", self.url)
            return self.json_message("Invalid JSON", status_code=400)

        _LOGGER.debug("Received Gira callback on %s: %s", self.url, data)

        api_client: GiraApiClient = (
            self.hass.data.get(DOMAIN, {}).get(self.entry_id, {}).get(DATA_API_CLIENT)
        )

        if not api_client or not api_client.token:
            _LOGGER.warning(
                "Callback received for unconfigured entry %s.", self.entry_id
            )
            return self.json_message("Client not configured", status_code=404)

        if data.get("token") != api_client.token:
            _LOGGER.error("Mismatched token in Gira callback. Ignoring.")
            return self.json_message("Token mismatch", status_code=404)

        await self.process_events(data.get("events", []), api_client)
        return self.json({}, status_code=200)

    @callback
    async def process_events(self, events: list, api_client: GiraApiClient):
        """Process the events from the callback."""
        raise NotImplementedError()


class GiraServiceCallbackView(BaseGiraCallbackView):
    """View to handle Gira Service Callbacks."""

    url = SERVICE_CALLBACK_PATH
    name = f"api:{DOMAIN}:service_callback"

    @callback
    async def process_events(self, events: list, api_client: GiraApiClient):
        """Process service events."""
        for event_data in events:
            event_type = event_data.get("event")
            _LOGGER.info("Processing Gira service event: %s", event_type)

            if event_type in (
                EVENT_TYPE_TEST,
                EVENT_TYPE_STARTUP,
                EVENT_TYPE_RESTART,
                EVENT_TYPE_PROJECT_CONFIG_CHANGED,
            ):
                _LOGGER.info("Received informational event: %s", event_type)
            elif event_type == EVENT_TYPE_UI_CONFIG_CHANGED:
                _LOGGER.warning(
                    "Gira device reported 'uiConfigChanged'. Triggering reload of the integration."
                )
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.entry_id)
                )
            else:
                _LOGGER.warning("Unknown Gira service event type: %s", event_type)


class GiraValueCallbackView(BaseGiraCallbackView):
    """View to handle Gira Value Callbacks."""

    url = VALUE_CALLBACK_PATH
    name = f"api:{DOMAIN}:value_callback"

    @callback
    async def process_events(self, events: list, api_client: GiraApiClient):
        """Process value update events."""
        for event_data in events:
            uid = event_data.get("uid")
            value = event_data.get("value")
            if uid is not None:
                _LOGGER.debug("Dispatching value update for UID %s", uid)
                async_dispatcher_send(
                    self.hass, f"{SIGNAL_DATA_UPDATE}_{self.entry_id}", uid, value
                )
