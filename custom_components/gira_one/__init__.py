"""Gira One integration."""

import logging
from typing import Any, Never

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import (
    GiraApiAuthError,
    GiraApiClient,
    GiraApiClientError,
    GiraApiConnectionError,
    GiraApiRequestError,
)
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DATA_API_CLIENT,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
    EVENT_TYPE_PROJECT_CONFIG_CHANGED,
    EVENT_TYPE_RESTART,
    EVENT_TYPE_STARTUP,
    EVENT_TYPE_TEST,
    EVENT_TYPE_UI_CONFIG_CHANGED,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_CALLBACK_PATH = f"/api/{DOMAIN}/service_callback"
VALUE_CALLBACK_PATH = f"/api/{DOMAIN}/value_callback"

# Used to signal entities that new data is available
SIGNAL_DATA_UPDATE = f"{DOMAIN}_data_update"


def _build_location_map(
    locations: list[dict[str, Any]], current_path: str = ""
) -> dict[str, str]:
    """Recursively build a map of function UID to location path (suggested area)."""
    location_map = {}
    for location in locations:
        name = location.get("displayName", "Unknown")
        # If we have a hierarchy, we could combine names, e.g. "Floor 1 / Living Room"
        # For HA Areas, usually the leaf name is what we want, or we let the user decide.
        # But Gira often has "Floor" -> "Room". Providing just "Room" is usually best for "Area".
        # If we want to be safe, we could do: full_name = f"{current_path} {name}".strip()
        # Let's try to just use the current location name as the Area.
        # If the user wants floors, they can manage that in HA.
        
        # Valid functions in this location
        for func in location.get("functions", []):
            uid = None
            if isinstance(func, str):
                uid = func
            elif isinstance(func, dict):
                uid = func.get("uid")
            
            if uid:
                location_map[uid] = name
        
        # Recursise into sub-locations
        if sub_locations := location.get("locations"):
            location_map.update(_build_location_map(sub_locations, name))
            
    return location_map


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gira One from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data["client_id"]
    access_token = entry.data["access_token"]

    @callback
    def _handle_auth_error() -> None:
        """Handle an authentication error by reloading the config entry."""
        _LOGGER.warning(
            "Gira API reported an authentication error. Reloading integration to refresh token."
        )
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    api_client = GiraApiClient(
        host, username, password, hass, auth_error_callback=_handle_auth_error
    )
    api_client.set_credentials(token=access_token, client_id=client_id)

    try:
        # Verify token is still valid by fetching UI config
        _LOGGER.debug("Attempting to fetch UI config with stored token.")
        ui_config = await api_client.get_ui_config()
        # Parse locations
        location_map = _build_location_map(ui_config.get("locations", []))
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
            location_map = _build_location_map(ui_config.get("locations", []))
        except (GiraApiAuthError, GiraApiRequestError) as e:
            _LOGGER.exception("Failed to re-register client: %s", e)
            return False
    except (GiraApiConnectionError, GiraApiRequestError) as e:
        _LOGGER.exception(
            "Failed to connect or communicate with Gira One Server: %s", e
        )
        return False  # Defer setup, HA will retry

    _LOGGER.info("Successfully connected to Gira IoT API and fetched UI config.")

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api_client,
        DATA_UI_CONFIG: ui_config,
        DATA_LOCATION_MAP: location_map,
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
        _LOGGER.exception(
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
        _LOGGER.exception("Failed to register Gira callbacks: %s", e)
        return False
    return True


async def _async_cleanup_resources(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Centralized function to clean up resources, unregister client and callbacks."""
    
    if DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]:
        return

    api_client: GiraApiClient = hass.data[DOMAIN][entry.entry_id].get(DATA_API_CLIENT)
    if not api_client:
        return

    # Prevent re-auth loop
    api_client.disable_auth_error_callback()

    _LOGGER.info("Cleaning up Gira One resources for %s", entry.title)
    try:
        await api_client.remove_callbacks()
    except GiraApiClientError as e:
        _LOGGER.exception("Error removing Gira callbacks: %s", e)
    try:
        await api_client.unregister_client()
    except GiraApiClientError as e:
        _LOGGER.exception("Error unregistering Gira client: %s", e)


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

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
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
    async def process_events(self, events: list, api_client: GiraApiClient) -> Never:
        """Process the events from the callback."""
        raise NotImplementedError


class GiraServiceCallbackView(BaseGiraCallbackView):
    """View to handle Gira Service Callbacks."""

    url = SERVICE_CALLBACK_PATH
    name = f"api:{DOMAIN}:service_callback"

    @callback
    async def process_events(self, events: list, api_client: GiraApiClient) -> None:
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
    async def process_events(self, events: list, api_client: GiraApiClient) -> None:
        """Process value update events."""
        for event_data in events:
            uid = event_data.get("uid")
            value = event_data.get("value")
            if uid is not None:
                _LOGGER.debug("Dispatching value update for UID %s", uid)
                async_dispatcher_send(
                    self.hass, f"{SIGNAL_DATA_UPDATE}_{self.entry_id}", uid, value
                )