"""Gira One integration."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
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
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_CALLBACK_PATH = f"/api/{DOMAIN}/service_callback"
VALUE_CALLBACK_PATH = f"/api/{DOMAIN}/value_callback"

# Used to signal entities that new data is available
SIGNAL_DATA_UPDATE = f"{DOMAIN}_data_update"


def _build_location_map(locations: list[dict[str, Any]]) -> dict[str, str]:
    """Recursively build a map of function UID to location name (suggested area).

    Only the leaf location's display name is used, which typically maps well
    to a Home Assistant Area (e.g. a room name).
    """
    location_map: dict[str, str] = {}
    for location in locations:
        name = location.get("displayName", "Unknown")

        for func in location.get("functions", []):
            uid: str | None = None
            if isinstance(func, str):
                uid = func
            elif isinstance(func, dict):
                uid = func.get("uid")

            if uid:
                location_map[uid] = name

        if sub_locations := location.get("locations"):
            location_map.update(_build_location_map(sub_locations))

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
        """Handle an authentication error reported by the API client.

        This starts a reauth flow so the user can re-enter credentials
        without having to delete and re-add the integration.
        """
        _LOGGER.warning(
            "Gira API reported an authentication error; starting reauth flow"
        )
        entry.async_start_reauth(hass)

    api_client = GiraApiClient(host, username, password, hass)
    api_client.set_credentials(token=access_token, client_id=client_id)

    try:
        _LOGGER.debug("Attempting to fetch UI config with stored token")
        ui_config = await api_client.get_ui_config()
    except GiraApiAuthError:
        _LOGGER.info(
            "Stored token is invalid or expired, attempting to re-register client"
        )
        try:
            new_token = await api_client.register_client(client_id)
        except GiraApiAuthError as reauth_err:
            raise ConfigEntryAuthFailed(
                f"Authentication with Gira One Server failed: {reauth_err}"
            ) from reauth_err
        except (GiraApiConnectionError, GiraApiRequestError) as retry_err:
            raise ConfigEntryNotReady(
                f"Could not re-register client with Gira One Server: {retry_err}"
            ) from retry_err

        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "access_token": new_token}
        )
        api_client.set_credentials(token=new_token, client_id=client_id)
        try:
            ui_config = await api_client.get_ui_config()
        except GiraApiClientError as retry_err:
            raise ConfigEntryNotReady(
                f"Failed to fetch UI config after re-registration: {retry_err}"
            ) from retry_err
    except (GiraApiConnectionError, GiraApiRequestError) as err:
        raise ConfigEntryNotReady(
            f"Failed to communicate with Gira One Server: {err}"
        ) from err

    location_map = _build_location_map(ui_config.get("locations", []))

    _LOGGER.info("Successfully connected to Gira IoT API and fetched UI config")

    # Activate auth error callback only after setup succeeds.
    # During setup, token refresh is handled inline — firing the callback
    # would start a spurious reauth flow.
    api_client.set_auth_error_callback(_handle_auth_error)

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api_client,
        DATA_UI_CONFIG: ui_config,
        DATA_LOCATION_MAP: location_map,
    }

    # Register hub device and clean up orphaned entries for removed functions
    await _async_register_device(hass, entry, api_client)
    _async_cleanup_stale_devices(hass, entry, ui_config)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register callback views with HA and callback URLs with the Gira device
    if not await _async_register_callbacks(hass, entry, api_client):
        raise ConfigEntryNotReady(
            "Could not register Gira One callback URLs. Ensure Home Assistant "
            "is reachable from the Gira One Server via an HTTPS URL"
        )

    # Ensure the Gira client is cleanly unregistered when HA shuts down
    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            lambda _event: hass.async_create_task(
                _async_cleanup_resources(hass, entry)
            ),
        )
    )

    return True


async def _async_register_device(
    hass: HomeAssistant, entry: ConfigEntry, api_client: GiraApiClient
) -> None:
    """Register the main Gira One hub device in the device registry."""
    try:
        server_details = await api_client.get_server_details()
    except GiraApiClientError as err:
        _LOGGER.warning("Could not fetch detailed server info: %s", err)
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


@callback
def _async_cleanup_stale_devices(
    hass: HomeAssistant, entry: ConfigEntry, ui_config: dict[str, Any]
) -> None:
    """Remove devices for Gira functions that no longer exist in the project."""
    device_registry = dr.async_get(hass)
    current_uids: set[str] = {
        function["uid"]
        for function in ui_config.get("functions", [])
        if function.get("uid")
        and function.get("functionType") in GIRA_FUNCTION_TYPE_TO_HA_PLATFORM
    }
    hub_identifier = entry.unique_id or entry.data[CONF_HOST]

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        gira_identifiers = [
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        ]
        if not gira_identifiers:
            continue
        # Keep the hub device itself
        if hub_identifier in gira_identifiers:
            continue
        if not any(identifier in current_uids for identifier in gira_identifiers):
            _LOGGER.info(
                "Removing stale Gira device %s (no longer in project config)",
                device.name,
            )
            device_registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def _async_register_callbacks(
    hass: HomeAssistant, entry: ConfigEntry, api_client: GiraApiClient
) -> bool:
    """Register callback URLs and views with Home Assistant and the Gira device."""
    try:
        base_url = get_url(
            hass,
            require_ssl=True,
            allow_internal=True,
            allow_external=True,
            prefer_external=True,
        )
    except NoURLAvailableError:
        _LOGGER.error(
            "Cannot determine an HTTPS URL for Gira callbacks. The Gira One "
            "Server only accepts HTTPS callback URLs. Configure either an "
            "internal_url or external_url with https:// in Home Assistant"
        )
        return False

    service_callback_url = f"{base_url}{SERVICE_CALLBACK_PATH}"
    value_callback_url = f"{base_url}{VALUE_CALLBACK_PATH}"

    _LOGGER.info("Registering Gira service callback URL: %s", service_callback_url)
    _LOGGER.info("Registering Gira value callback URL: %s", value_callback_url)

    hass.http.register_view(GiraServiceCallbackView(hass, entry.entry_id))
    hass.http.register_view(GiraValueCallbackView(hass, entry.entry_id))

    try:
        await api_client.register_callbacks(service_callback_url, value_callback_url)
    except GiraApiClientError as err:
        _LOGGER.error("Failed to register Gira callbacks: %s", err)
        return False

    _LOGGER.info("Gira callbacks registered successfully")
    return True


async def _async_cleanup_resources(
    hass: HomeAssistant, entry: ConfigEntry, *, unregister: bool = False
) -> None:
    """Remove callbacks and optionally unregister the client.

    By default the client is kept registered so the stored token remains
    valid across restarts.  Pass ``unregister=True`` only when the config
    entry is being permanently removed.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not entry_data:
        return

    api_client: GiraApiClient | None = entry_data.get(DATA_API_CLIENT)
    if api_client is None:
        return

    # Prevent re-auth loop during cleanup
    api_client.disable_auth_error_callback()

    _LOGGER.info("Cleaning up Gira One resources for %s", entry.title)
    try:
        await api_client.remove_callbacks()
    except GiraApiClientError as err:
        _LOGGER.warning("Error removing Gira callbacks: %s", err)

    if unregister:
        try:
            await api_client.unregister_client()
        except GiraApiClientError as err:
            _LOGGER.warning("Error unregistering Gira client: %s", err)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unregister the client when the config entry is permanently removed."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data.get("client_id")
    access_token = entry.data.get("access_token")

    if not access_token or not client_id:
        return

    api_client = GiraApiClient(host, username, password, hass)
    api_client.set_credentials(token=access_token, client_id=client_id)
    try:
        await api_client.unregister_client()
    except GiraApiClientError:
        _LOGGER.warning("Could not unregister client during entry removal")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Gira One entry: %s", entry.title)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await _async_cleanup_resources(hass, entry)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("Gira One entry %s unloaded successfully", entry.title)

    return unload_ok


class BaseGiraCallbackView(HomeAssistantView):
    """Base view for Gira callbacks."""

    requires_auth = False
    url = "OVERRIDE_IN_SUBCLASS"
    name = "OVERRIDE_IN_SUBCLASS_WITH_API_PREFIX"
    cors_allowed = True

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the callback view."""
        self.hass = hass
        self.entry_id = entry_id

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST requests from the Gira API."""
        try:
            data = await request.json()
        except ValueError:
            _LOGGER.warning("Received invalid JSON in Gira callback %s", self.url)
            return self.json_message("Invalid JSON", status_code=400)

        _LOGGER.debug("Received Gira callback on %s: %s", self.url, data)

        api_client: GiraApiClient | None = (
            self.hass.data.get(DOMAIN, {}).get(self.entry_id, {}).get(DATA_API_CLIENT)
        )

        if not api_client or not api_client.token:
            _LOGGER.warning(
                "Callback received for unconfigured entry %s", self.entry_id
            )
            return self.json_message("Client not configured", status_code=404)

        if data.get("token") != api_client.token:
            _LOGGER.error("Mismatched token in Gira callback; ignoring")
            return self.json_message("Token mismatch", status_code=404)

        await self.process_events(data.get("events", []), api_client)
        return self.json({}, status_code=200)

    async def process_events(self, events: list, api_client: GiraApiClient) -> None:
        """Process the events from the callback."""
        raise NotImplementedError


class GiraServiceCallbackView(BaseGiraCallbackView):
    """View to handle Gira service callbacks."""

    url = SERVICE_CALLBACK_PATH
    name = f"api:{DOMAIN}:service_callback"

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
                    "Gira device reported 'uiConfigChanged'; reloading the integration"
                )
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.entry_id)
                )
            else:
                _LOGGER.warning("Unknown Gira service event type: %s", event_type)


class GiraValueCallbackView(BaseGiraCallbackView):
    """View to handle Gira value callbacks."""

    url = VALUE_CALLBACK_PATH
    name = f"api:{DOMAIN}:value_callback"

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
