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
)
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    DATA_LISTENERS,
    DATA_ACCESS_TOKEN,
    EVENT_TYPE_UI_CONFIG_CHANGED,
    EVENT_TYPE_RESTART,
    EVENT_TYPE_STARTUP,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_CALLBACK_PATH = f"/api/{DOMAIN}/service_callback"
VALUE_CALLBACK_PATH = f"/api/{DOMAIN}/value_callback"

# Used to signal entities that new data is available
SIGNAL_DATA_UPDATE = f"{DOMAIN}_data_update"
# Used to signal that UI config has changed and entities might need to be reloaded
SIGNAL_UI_CONFIG_CHANGED = f"{DOMAIN}_ui_config_changed"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gira One from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data.get("client_id") # From config flow
    access_token = entry.data.get("access_token") # Initial token from config flow


    api_client = GiraApiClient(host, username, password, hass)

    if access_token and client_id:
        # If we have a token from config_flow, try to use it.
        # The GiraApiClient needs a way to be initialized with an existing token.
        api_client._token = access_token # Direct access for now, consider setter
        api_client._client_id = client_id
        _LOGGER.info("Using existing token and client_id from config entry.")
    else:
        _LOGGER.error("Client ID or access token missing from config entry. This should not happen.")
        return False


    try:
        # Verify token is still valid by fetching UI config, or re-register if needed
        try:
            _LOGGER.debug("Attempting to fetch UI config with stored token.")
            ui_config = await api_client.get_ui_config()
        except GiraApiAuthError: # Token might be invalid
            _LOGGER.warning("Initial token seems invalid or expired, attempting to re-register client.")
            new_token = await api_client.register_client(client_id) # Re-register
            if new_token:
                # Persist the new token
                new_data = {**entry.data, "access_token": new_token}
                hass.config_entries.async_update_entry(entry, data=new_data)
                _LOGGER.info("Client re-registered successfully, new token stored.")
                ui_config = await api_client.get_ui_config() # Retry with new token
            else:
                raise GiraApiAuthError("Failed to re-register client and obtain a new token.")

        _LOGGER.info("Successfully connected to Gira IoT API and fetched UI config.")

    except GiraApiAuthError:
        _LOGGER.error("Authentication failed for Gira IoT API. Please check credentials.")
        return False
    except (GiraApiConnectionError, GiraApiRequestError) as e:
        _LOGGER.error("Failed to connect or communicate with Gira One Server: %s", e)
        return False # Defer setup, HA will retry

    hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT] = api_client
    hass.data[DOMAIN][entry.entry_id][DATA_UI_CONFIG] = ui_config
    hass.data[DOMAIN][entry.entry_id][DATA_LISTENERS] = []

    # Register device
    device_name = ui_config.get("deviceName", host) # From API availability check, fallback to host
    device_type = ui_config.get("deviceType", "Unknown Gira Device")
    device_version = ui_config.get("deviceVersion", "Unknown")

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id or host)}, # entry.unique_id is host from config_flow
        name=device_name,
        manufacturer="Gira",
        model=device_type,
        sw_version=device_version,
    )

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register callback views
    try:
        # Get external URL for callbacks. Gira IoT API requires HTTPS for callbacks.
        # This will be like https://<your_ha_domain_or_ip>:<port>
        base_url = get_url(hass, require_ssl=True, allow_internal=False, prefer_external=True)
    except NoURLAvailableError:
        _LOGGER.error(
            "Cannot determine external SSL URL for Gira callbacks. "
            "Please ensure Home Assistant is configured with an external URL and SSL."
        )
        # Not returning False, as basic polling might still work if implemented later,
        # but callbacks will fail to register.
        # However, the prompt *requires* callbacks.
        return False


    service_callback_url = f"{base_url}{SERVICE_CALLBACK_PATH}"
    value_callback_url = f"{base_url}{VALUE_CALLBACK_PATH}"

    _LOGGER.info("Registering Gira Service Callback URL: %s", service_callback_url)
    _LOGGER.info("Registering Gira Value Callback URL: %s", value_callback_url)

    hass.http.register_view(GiraServiceCallbackView(hass, entry.entry_id))
    hass.http.register_view(GiraValueCallbackView(hass, entry.entry_id))

    try:
        await api_client.register_callbacks(service_callback_url, value_callback_url)
        _LOGGER.info("Gira callbacks registered successfully with the device.")
    except GiraApiRequestError as e:
        _LOGGER.error("Failed to register Gira callbacks with the device: %s. Real-time updates will not work.", e)
        # Depending on strictness, you might want to return False here
        # For now, let's allow it to load but with a clear error.
    except Exception as e:
        _LOGGER.error("Unexpected error registering Gira callbacks: %s", e)
        return False


    # Listener for Home Assistant stop event to unregister client and callbacks
    async def async_on_hass_stop(event):
        """Run when Home Assistant is stopping."""
        _LOGGER.info("Home Assistant is stopping. Cleaning up Gira One resources.")
        client: GiraApiClient = hass.data[DOMAIN][entry.entry_id].get(DATA_API_CLIENT)
        if client:
            try:
                await client.remove_callbacks()
            except Exception as e:
                _LOGGER.error("Error removing Gira callbacks: %s", e)
            try:
                await client.unregister_client()
            except Exception as e:
                _LOGGER.error("Error unregistering Gira client: %s", e)

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_on_hass_stop)
    )
    # Store this listener to be able to remove it on unload
    hass.data[DOMAIN][entry.entry_id][DATA_LISTENERS].append(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_on_hass_stop)
    )


    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Gira One entry: %s", entry.entry_id)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Clean up listeners
    listeners = hass.data[DOMAIN][entry.entry_id].get(DATA_LISTENERS, [])
    for unsub_listener in listeners:
        unsub_listener()

    # Clean up resources
    api_client: GiraApiClient = hass.data[DOMAIN][entry.entry_id].get(DATA_API_CLIENT)
    if api_client:
        try:
            _LOGGER.info("Attempting to remove callbacks from Gira device.")
            await api_client.remove_callbacks()
        except Exception as e:
            _LOGGER.error("Error removing Gira callbacks during unload: %s", e)
        try:
            _LOGGER.info("Attempting to unregister client from Gira device.")
            await api_client.unregister_client()
        except Exception as e:
            _LOGGER.error("Error unregistering Gira client during unload: %s", e)


    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.info("Gira One entry %s unloaded successfully.", entry.entry_id)

    return unload_ok


class BaseGiraCallbackView(HomeAssistantView):
    """Base View for Gira Callbacks."""
    requires_auth = False  # Gira API sends this without HA auth
    url = "OVERRIDE_IN_SUBCLASS" # e.g. SERVICE_CALLBACK_PATH
    name = "OVERRIDE_IN_SUBCLASS_WITH_API_PREFIX" # e.g. "api:gira_one:service_callback"
    cors_allowed = True

    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self.entry_id = entry_id # To know which config entry this callback belongs to

    async def post(self, request):
        """Handle POST requests from Gira API."""
        try:
            data = await request.json()
        except ValueError:
            _LOGGER.warning("Received invalid JSON in Gira callback %s", self.url)
            return self.json_message("Invalid JSON", status_code=400)

        _LOGGER.debug("Received Gira callback on %s: %s", self.url, data)

        # Verify token if present in callback data
        # This is important to ensure the callback is for our registered client
        expected_token = None
        api_client: GiraApiClient = self.hass.data.get(DOMAIN, {}).get(self.entry_id, {}).get(DATA_API_CLIENT)

        if api_client:
            expected_token = api_client.token

        if not expected_token:
            _LOGGER.warning("No API client or token found for entry %s to verify callback.", self.entry_id)
            # According to API documentation, if client responds with 404, API implicitly unregisters client
            # This seems like a good place if we can't find our client.
            return self.json_message("Client not configured or token missing", status_code=404)

        callback_token = data.get("token")
        if callback_token != expected_token:
            _LOGGER.error(
                "Mismatched token in Gira callback. Expected '%s', got '%s'. Ignoring.",
                expected_token, callback_token
            )
            # If token is mismatched, it's a security concern.
            # API documentation specifies 404 for client de-registration
            return self.json_message("Token mismatch", status_code=404)

        # Process the events
        await self.process_events(data.get("events", []), api_client)

        return self.json({}, status_code=200) # Always return 200 OK if processed

    @callback
    async def process_events(self, events: list, api_client: GiraApiClient):
        """Process the events from the callback. To be implemented by subclasses."""
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

            if event_type == EVENT_TYPE_TEST:
                _LOGGER.info("Received 'test' service event from Gira API.")
            elif event_type == EVENT_TYPE_STARTUP:
                _LOGGER.info("Gira device reported 'startup'.")
                # Potentially re-verify connection or fetch fresh status
            elif event_type == EVENT_TYPE_RESTART:
                _LOGGER.info("Gira device reported 'restart'.")
                # Might need to re-register callbacks after restart, or API client.
            elif event_type == EVENT_TYPE_PROJECT_CONFIG_CHANGED:
                _LOGGER.info("Gira device reported 'projectConfigChanged'. UI config might not have changed.")
                # As per API documentation, direct reaction not useful as server might be locked]
            elif event_type == EVENT_TYPE_UI_CONFIG_CHANGED:
                _LOGGER.warning("Gira device reported 'uiConfigChanged'. Reloading configuration.")
                # This is a significant event. We need to fetch the new UI config and
                # potentially remove/re-add entities. This is complex.
                # Simplest is to trigger a reload of the config entry.
                try:
                    new_ui_config = await api_client.get_ui_config()
                    self.hass.data[DOMAIN][self.entry_id][DATA_UI_CONFIG] = new_ui_config
                    # Signal that entities need to check for changes or be reloaded
                    async_dispatcher_send(self.hass, f"{SIGNAL_UI_CONFIG_CHANGED}_{self.entry_id}")
                    _LOGGER.info("Successfully re-fetched UI config after 'uiConfigChanged' event.")
                    # A full reload might be safer:
                    # await self.hass.config_entries.async_reload(self.entry_id)
                except Exception as e:
                    _LOGGER.error("Failed to re-fetch UI config after 'uiConfigChanged' event: %s", e)
            else:
                _LOGGER.warning("Unknown Gira service event type: %s", event_type)


class GiraValueCallbackView(BaseGiraCallbackView):
    """View to handle Gira Value Callbacks."""
    url = VALUE_CALLBACK_PATH
    name = f"api:{DOMAIN}:value_callback"

    @callback
    async def process_events(self, events: list, api_client: GiraApiClient):
        """Process value update events."""
        # Example: events = [{"uid": "<unique identifier of data point>", "value": "<new data point value>"}]
        for event_data in events:
            uid = event_data.get("uid")
            value = event_data.get("value")
            _LOGGER.debug("Processing Gira value event for UID %s: %s", uid, value)
            if uid is not None:
                # Dispatch the update. Entities will listen for this.
                # The signal includes the entry_id to ensure only relevant entities update.
                async_dispatcher_send(self.hass, f"{SIGNAL_DATA_UPDATE}_{self.entry_id}", uid, value)