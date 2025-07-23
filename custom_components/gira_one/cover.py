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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient
from .const import (
    COVER,
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    DP_MOVEMENT,
    DP_POSITION,
    DP_SLAT_POSITION,
    DP_STEP_UP_DOWN,
    DP_UP_DOWN,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
)
from .entity import GiraOneEntity  # Import the new base class

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Cover entities from a config entry."""
    api_client: GiraApiClient = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_API_CLIENT
    ]
    ui_config: Dict[str, Any] = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_UI_CONFIG
    ]

    entities = []
    for function_data in ui_config.get("functions", []):
        if GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType")) == COVER:
            entities.append(GiraCover(config_entry, api_client, function_data))
            _LOGGER.info(
                "Adding Gira Cover: %s (UID: %s)",
                function_data.get("displayName"),
                function_data.get("uid"),
            )
    async_add_entities(entities)


class GiraCover(GiraOneEntity, CoverEntity):
    """Representation of a Gira Cover."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: Dict[str, Any],
    ) -> None:
        """Initialize the Gira Cover."""
        # Call the parent's __init__ to handle all the boilerplate
        super().__init__(config_entry, api_client, function_data)

        # Cover-specific attributes
        self._attr_current_cover_position: Optional[int] = None
        self._attr_current_cover_tilt_position: Optional[int] = None
        self._attr_is_moving: bool = False
        self._attr_is_opening: bool = False
        self._attr_is_closing: bool = False
        self._attr_is_closed: Optional[bool] = None
        self._last_known_direction_is_opening: Optional[bool] = None

        self._update_supported_features()

    def _update_supported_features(self) -> None:
        """Determine supported features based on available data points."""
        features = CoverEntityFeature(0)
        if self._has_dp(DP_UP_DOWN):
            features |= (
                CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
            )
        if self._can_write_dp(DP_POSITION):
            features |= CoverEntityFeature.SET_POSITION
        if self._can_write_dp(DP_SLAT_POSITION):
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
                | CoverEntityFeature.STOP_TILT
            )
        self._attr_supported_features = features

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the cover."""
        try:
            data = await self._api.get_value(self.unique_id)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_dp_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
        except Exception as e:
            _LOGGER.error("Error fetching initial state for cover %s: %s", self.name, e)

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state. Returns True if state changed."""
        changed = False
        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                try:
                    if dp_name == DP_POSITION:
                        gira_position = int(float(value))
                        ha_position = 100 - gira_position  # Invert logic
                        if self._attr_current_cover_position != ha_position:
                            self._attr_current_cover_position = ha_position
                            self._attr_is_closed = self._attr_current_cover_position == 100
                            changed = True
                    elif dp_name == DP_SLAT_POSITION:
                        gira_tilt = int(float(value))
                        ha_tilt = 100 - gira_tilt  # Invert logic
                        if self._attr_current_cover_tilt_position != ha_tilt:
                            self._attr_current_cover_tilt_position = ha_tilt
                            changed = True
                    elif dp_name == DP_MOVEMENT:
                        is_moving_now = bool(int(value))
                        if self._attr_is_moving != is_moving_now:
                            self._attr_is_moving = is_moving_now
                            changed = True
                            if not is_moving_now:
                                self._attr_is_opening = False
                                self._attr_is_closing = False
                except (ValueError, TypeError):
                    return False
                return changed
        return False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self.async_set_cover_position(position=0)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self.async_set_cover_position(position=100)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        if self._has_dp(DP_STEP_UP_DOWN):
            await self._send_command(DP_STEP_UP_DOWN, 1) # Send a step command to stop
        else:
            _LOGGER.warning("Cover %s: No DP_STEP_UP_DOWN found for stopping.", self.name)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the cover position."""
        ha_position = kwargs[ATTR_POSITION]
        gira_position = 100 - ha_position  # Invert logic for sending
        await self._send_command(DP_POSITION, gira_position)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the cover tilt."""
        await self.async_set_cover_tilt_position(tilt_position=0)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the cover tilt."""
        await self.async_set_cover_tilt_position(tilt_position=100)

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop the cover tilt."""
        if self._has_dp(DP_STEP_UP_DOWN):
            await self._send_command(DP_STEP_UP_DOWN, 1)
        else:
            _LOGGER.warning("Cover %s: No DP_STEP_UP_DOWN found for stopping tilt.", self.name)

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the cover tilt position."""
        ha_tilt_position = kwargs[ATTR_TILT_POSITION]
        gira_tilt_position = 100 - ha_tilt_position  # Invert logic for sending
        await self._send_command(DP_SLAT_POSITION, gira_tilt_position)