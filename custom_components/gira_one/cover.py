"""Platform for Gira One Cover entities."""

import logging
from typing import Any, Dict, Optional

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient
from .const import (
    DOMAIN,
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    COVER,
    DP_POSITION,
    DP_SLAT_POSITION,
    DP_UP_DOWN,
    DP_STEP_UP_DOWN,
    DP_MOVEMENT,
)
from . import SIGNAL_DATA_UPDATE, SIGNAL_UI_CONFIG_CHANGED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Cover entities from a config entry."""
    api_client: GiraApiClient = hass.data[DOMAIN][config_entry.entry_id][
        DATA_API_CLIENT
    ]
    ui_config: Dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id][DATA_UI_CONFIG]

    entities = []
    gira_functions = ui_config.get("functions", [])

    for function_data in gira_functions:
        function_type = function_data.get("functionType")
        platform = GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_type)

        if platform == COVER:
            entities.append(GiraCover(config_entry, api_client, function_data))
            _LOGGER.info(
                "Adding Gira Cover: %s (UID: %s)",
                function_data.get("displayName"),
                function_data.get("uid"),
            )

    async_add_entities(entities)

    # Listener for UI config changes (simplified)
    @callback
    def _async_reload_entities():
        _LOGGER.info(
            "Gira UI config changed, cover entities might need reload for %s.",
            config_entry.entry_id,
        )
        # Full reload is typically managed by __init__.py from service callback.

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_UI_CONFIG_CHANGED}_{config_entry.entry_id}",
            _async_reload_entities,
        )
    )


class GiraCover(CoverEntity):
    """Representation of a Gira Cover."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: Dict[str, Any],
    ) -> None:
        """Initialize the Gira Cover."""
        self._config_entry_id = config_entry.entry_id
        self._api = api_client
        self._function_data = function_data

        self._attr_unique_id = function_data["uid"]
        self._attr_name = function_data.get(
            "displayName", f"Gira Cover {self._attr_unique_id}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer="Gira",
            model=function_data.get(
                "channelType"
            ),  # e.g., de.gira.schema.channels.BlindWithPos
            via_device=(DOMAIN, config_entry.unique_id or config_entry.data[CONF_HOST]),
            sw_version=function_data.get("functionType"),
        )

        self._data_points: Dict[str, Dict[str, Any]] = {
            dp["name"]: dp for dp in function_data.get("dataPoints", [])
        }

        self._attr_current_cover_position: Optional[int] = (
            None  # 0 (open) to 100 (closed)
        )
        self._attr_current_cover_tilt_position: Optional[int] = (
            None  # 0 (open) to 100 (closed)
        )
        self._attr_is_moving: bool = False
        self._attr_is_opening: bool = (
            False  # Requires knowing direction if _attr_is_moving is true
        )
        self._attr_is_closing: bool = False  # Requires knowing direction
        self._attr_is_closed: Optional[bool] = None

        self._last_known_direction_is_opening: Optional[bool] = None

        self._update_supported_features()
        _LOGGER.debug(
            "Initialized GiraCover %s with DPs: %s, Features: %s",
            self.name,
            self._data_points.keys(),
            self.supported_features,
        )

    def _update_supported_features(self) -> None:
        """Determine supported features based on available data points."""
        features = CoverEntityFeature(0)
        # Basic open/close/stop
        if DP_UP_DOWN in self._data_points:  #
            features |= (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
            )

        # Position control
        if DP_POSITION in self._data_points and self._data_points[DP_POSITION].get(
            "canWrite", False
        ):  #
            features |= CoverEntityFeature.SET_POSITION

        # Tilt control
        if DP_SLAT_POSITION in self._data_points and self._data_points[
            DP_SLAT_POSITION
        ].get("canWrite", False):  #
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
            # If Slat-Position exists, STOP_TILT is usually also implied/supported.
            features |= CoverEntityFeature.STOP_TILT

        # If SET_POSITION is supported, basic OPEN/CLOSE are implied by setting position to 0/100
        # However, HA still expects the flags for dedicated buttons.

        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        """Register for callback when entity is added."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_UPDATE}_{self._config_entry_id}",
                self._handle_value_update,
            )
        )
        await self._fetch_initial_state()

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the cover."""
        try:
            _LOGGER.debug(
                "Requesting initial state for cover function UID %s", self.unique_id
            )
            data = await self._api.get_value(
                self.unique_id
            )  # Get all DPs for this function
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
                self.async_write_ha_state()
                _LOGGER.debug("Initial state updated for cover %s", self.name)
        except Exception as e:
            _LOGGER.error(
                "Error fetching initial state for cover %s (UID: %s): %s",
                self.name,
                self.unique_id,
                e,
            )

    @callback
    def _handle_value_update(self, dp_uid_updated: str, value: Any) -> None:
        """Handle a value update from the Gira callback."""
        if self._update_state_from_value(dp_uid_updated, value):
            self.async_write_ha_state()

    def _update_state_from_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state. Returns True if state changed."""
        changed = False
        parsed_value = None

        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                try:
                    if dp_name == DP_POSITION:  #
                        parsed_value = int(
                            float(value)
                        )  # Gira 0-100, HA 0-100 (0=open, 100=closed)
                        if self._attr_current_cover_position != parsed_value:
                            self._attr_current_cover_position = parsed_value
                            self._attr_is_closed = (
                                self._attr_current_cover_position == 100
                            )
                            changed = True
                    elif dp_name == DP_SLAT_POSITION:  #
                        parsed_value = int(
                            float(value)
                        )  # Gira 0-100, HA 0-100 (0=open, 100=closed)
                        if self._attr_current_cover_tilt_position != parsed_value:
                            self._attr_current_cover_tilt_position = parsed_value
                            changed = True
                    elif dp_name == DP_MOVEMENT:  #
                        # Binary: 1 = moving, 0 = stopped (assumption)
                        is_moving_now = bool(int(value))
                        if self._attr_is_moving != is_moving_now:
                            self._attr_is_moving = is_moving_now
                            changed = True
                            if not is_moving_now:  # Just stopped
                                self._attr_is_opening = False
                                self._attr_is_closing = False
                                self._last_known_direction_is_opening = None
                            elif self._last_known_direction_is_opening is not None:
                                # We know the direction it started moving
                                self._attr_is_opening = (
                                    self._last_known_direction_is_opening
                                )
                                self._attr_is_closing = (
                                    not self._last_known_direction_is_opening
                                )
                    # Up-Down and Step-Up-Down are write-only according to
                    # So no state updates from them directly.

                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Could not parse value '%s' for cover DP %s (%s): %s",
                        value,
                        dp_name,
                        dp_uid_updated,
                        e,
                    )
                    return False

                if changed:
                    _LOGGER.debug(
                        "Cover %s state change: Position=%s, Tilt=%s, Moving=%s, IsClosed=%s",
                        self.name,
                        self._attr_current_cover_position,
                        self._attr_current_cover_tilt_position,
                        self._attr_is_moving,
                        self._attr_is_closed,
                    )
                return changed
        return False

    async def _send_command(self, dp_name: str, value: Any) -> None:
        """Helper to send a command to a data point."""
        if dp_name not in self._data_points:
            _LOGGER.error("Cover %s: Data point '%s' not found.", self.name, dp_name)
            return
        dp_uid = self._data_points[dp_name]["uid"]
        _LOGGER.debug(
            "Cover %s: Sending to DP '%s' (UID %s) value '%s'",
            self.name,
            dp_name,
            dp_uid,
            value,
        )
        try:
            await self._api.set_value(dp_uid, value)
        except Exception as e:
            _LOGGER.error(
                "Cover %s: Error sending command to DP '%s': %s", self.name, dp_name, e
            )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        _LOGGER.debug(
            "Cover %s: Opening (current pos: %s)",
            self.name,
            self._attr_current_cover_position,
        )
        self._attr_is_opening = True  # Optimistic
        self._attr_is_closing = False
        self._last_known_direction_is_opening = True
        if (
            self.supported_features & CoverEntityFeature.SET_POSITION
            and DP_POSITION in self._data_points
        ):
            await self._send_command(DP_POSITION, 0)  # 0 is open for HA
        elif DP_UP_DOWN in self._data_points:
            # Assuming 0 for UP for Up-Down data point
            await self._send_command(DP_UP_DOWN, 0)
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        _LOGGER.debug(
            "Cover %s: Closing (current pos: %s)",
            self.name,
            self._attr_current_cover_position,
        )
        self._attr_is_closing = True  # Optimistic
        self._attr_is_opening = False
        self._last_known_direction_is_opening = False
        if (
            self.supported_features & CoverEntityFeature.SET_POSITION
            and DP_POSITION in self._data_points
        ):
            await self._send_command(DP_POSITION, 100)  # 100 is closed for HA
        elif DP_UP_DOWN in self._data_points:
            # Assuming 1 for DOWN for Up-Down data point
            await self._send_command(DP_UP_DOWN, 1)
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        _LOGGER.debug("Cover %s: Stopping", self.name)
        # For KNX, stopping is often done by sending a "stop" command to the "Step" GA,
        # or re-sending the current target position if positionable.
        # The Gira API doc  marks Up-Down and Step-Up-Down as -W- (Write Only).
        # If Position is settable, resending current position is a common stop method.
        if (
            self.supported_features & CoverEntityFeature.SET_POSITION
            and DP_POSITION in self._data_points
            and self._attr_current_cover_position is not None
        ):
            _LOGGER.debug(
                "Stopping cover %s by setting current position %s",
                self.name,
                self._attr_current_cover_position,
            )
            await self._send_command(DP_POSITION, self._attr_current_cover_position)
        elif DP_UP_DOWN in self._data_points:
            # This is speculative: sending a dedicated stop value if the API supports it on Up-Down.
            # A common KNX pattern for a single "Up/Down/Stop" object might use 0=Up, 1=Down, 2=Stop.
            # The Gira API documentation is not explicit here for "Binary".
            # Another common pattern for KNX is to send the *same* Up or Down command again to stop.
            # This is risky without knowing the exact Gira API behavior for this DP.
            # For now, we can try sending a value that typically means stop in some systems (e.g., 2)
            # Or, if we knew the last command (0 for up, 1 for down), send that again.
            # This part needs validation against actual Gira behavior.
            _LOGGER.warning(
                "Cover %s: Stop functionality for Up-Down DP is ambiguous. Attempting a common stop value (e.g., if 'Up-Down' also accepts a stop command). This might not work.",
                self.name,
            )
            # Example: await self._send_command(DP_UP_DOWN, 2) # Assuming 2 is stop, highly speculative
            # A safer bet if position is not available might be to use Step-Up-Down if that implies stop.
            if DP_STEP_UP_DOWN in self._data_points:
                # Assume sending to Step-Up-Down also implies stop if it's a toggle or dedicated stop.
                # Gira docs: 'Step-Up-Down Binary M -W-'.
                # Typically, a 'step' command for KNX is brief. It might not be a stop command.
                _LOGGER.info(
                    "Cover %s: Stop via Up-Down is not clearly defined by Gira API docs. Position based stop is preferred.",
                    self.name,
                )
            else:
                _LOGGER.info(
                    "Cover %s: No clear stop mechanism if SET_POSITION is not available.",
                    self.name,
                )

        # Optimistic state update for stop
        self._attr_is_moving = False
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._last_known_direction_is_opening = None
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the cover position."""
        position = kwargs[ATTR_POSITION]
        _LOGGER.debug("Cover %s: Setting position to %s", self.name, position)
        await self._send_command(DP_POSITION, position)
        # Optimistic update, callback will confirm
        self._attr_current_cover_position = position
        self._attr_is_closed = position == 100
        self.async_write_ha_state()

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the cover tilt."""
        _LOGGER.debug("Cover %s: Opening tilt", self.name)
        await self._send_command(DP_SLAT_POSITION, 0)  # 0 is open for HA
        self._attr_current_cover_tilt_position = 0  # Optimistic
        self.async_write_ha_state()

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the cover tilt."""
        _LOGGER.debug("Cover %s: Closing tilt", self.name)
        await self._send_command(DP_SLAT_POSITION, 100)  # 100 is closed for HA
        self._attr_current_cover_tilt_position = 100  # Optimistic
        self.async_write_ha_state()

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop the cover tilt."""
        # Similar to stop_cover, resend current tilt position if available.
        _LOGGER.debug("Cover %s: Stopping tilt", self.name)
        if (
            self.supported_features & CoverEntityFeature.SET_TILT_POSITION
            and DP_SLAT_POSITION in self._data_points
            and self._attr_current_cover_tilt_position is not None
        ):
            await self._send_command(
                DP_SLAT_POSITION, self._attr_current_cover_tilt_position
            )
        else:
            _LOGGER.warning(
                "Cover %s: Cannot stop tilt without SET_TILT_POSITION support or known current tilt.",
                self.name,
            )
        self.async_write_ha_state()  # Assuming stop means movement ceases

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the cover tilt position."""
        tilt_position = kwargs[ATTR_TILT_POSITION]
        _LOGGER.debug("Cover %s: Setting tilt position to %s", self.name, tilt_position)
        await self._send_command(DP_SLAT_POSITION, tilt_position)
        self._attr_current_cover_tilt_position = tilt_position  # Optimistic
        self.async_write_ha_state()
