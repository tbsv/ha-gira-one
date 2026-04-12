# Gira One Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/tbsv/ha-gira-one?display_name=tag&sort=semver)](https://github.com/tbsv/ha-gira-one/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Validate](https://github.com/tbsv/ha-gira-one/actions/workflows/validate.yml/badge.svg)](https://github.com/tbsv/ha-gira-one/actions/workflows/validate.yml)

This integration connects Home Assistant to a **Gira One Server** via its local REST API. It is based on the [Gira IoT REST API specification](https://partner.gira.de/data3/Gira_IoT_REST_API_v2_DE.pdf).
It exposes lights, covers, and room thermostats as native Home Assistant entities and receives real-time status updates via callbacks (Local Push), so state changes made at the physical switch or in the Gira app are immediately reflected in Home Assistant.

> [!IMPORTANT]
> **Home Assistant must be reachable from the Gira One Server via an HTTPS URL.** The Gira One Server pushes state updates back to Home Assistant via HTTP callbacks and explicitly rejects non-HTTPS targets. Either an *internal* or an *external* URL works, as long as it uses `https://` and the Gira Server can reach it on the network. The integration will refuse to set up if no HTTPS URL is configured.

## Features

| Platform | Capabilities |
|---|---|
| **Switch** | On/off |
| **Light** | On/off, brightness (dimming), color temperature (tunable white), RGB/W color |
| **Cover** | Open, close, stop, set position, set tilt (slat) position — for roller shutters and venetian blinds |
| **Climate** | Current temperature, target temperature, HVAC mode, preset modes (Comfort, Eco/Night, Away/Standby, Protection) |
| **Sensor** | Current and target temperature exposed as dedicated sensor entities per Gira climate function (for graphing and automations) |

### Supported Gira function types

Behind the scenes, the following Gira function types are mapped automatically:

- `de.gira.schema.functions.Switch` → Switch
- `de.gira.schema.functions.KNX.Light` → Light
- `de.gira.schema.functions.ColoredLight` → Light
- `de.gira.schema.functions.TunableLight` → Light
- `de.gira.schema.functions.Covering` → Cover
- `de.gira.schema.functions.KNX.HeatingCooling` → Climate
- `de.gira.schema.functions.KNX.FanCoil` → Climate
- `de.gira.schema.functions.SaunaHeating` → Climate

## Prerequisites

1. A **Gira One Server** accessible on your local network.
2. A **user account** on the Gira One Server with permissions to control the target devices.
3. **Home Assistant reachable via an HTTPS URL** that the Gira One Server can hit (internal or external — see important note above).

## Installation

### Via HACS (recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed.
2. In HACS → *Integrations*, click the three-dot menu → *Custom repositories*.
3. Add this repository URL and select *Integration* as category.
4. Install **Gira One** and restart Home Assistant.

### Manual

1. Copy the `custom_components/gira_one` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

After installation, add the integration via the UI:

1. Go to **Settings → Devices & Services**.
2. Click **+ Add Integration** and search for *Gira One*.
3. Enter:
   - **Host**: IP address or hostname of your Gira One Server.
   - **Username** / **Password**: A Gira One user account with sufficient permissions.
4. Submit.

Compatible devices will be added automatically and grouped by room (using the Gira project's location names as suggested areas).

## Troubleshooting

**Setup fails with "Cannot determine an HTTPS URL".**
Home Assistant has neither an internal nor external HTTPS URL configured. Set one under *Settings → System → Network → Home Assistant URL* and make sure it uses `https://`. A local HTTPS URL is fine as long as the Gira One Server can reach it and accepts the TLS certificate (self-signed certificates may be rejected — use a private CA or a trusted certificate in that case).

**Setup fails with "invalid_auth".**
Double-check the Gira One username and password. Note that some Gira user roles cannot register new API clients.

**Setup fails with "device_locked" (HTTP 423).**
The Gira One Server is currently being configured from the Gira project planner or is otherwise locked. Wait until the lock is released and retry.

**State changes from the Gira side don't show up in Home Assistant.**
The Gira Server cannot reach Home Assistant on the callback URL. Verify:
- Your external URL uses HTTPS with a certificate trusted by the Gira Server.
- Port forwarding / reverse proxy passes through to Home Assistant.
- No firewall rule is blocking outbound HTTPS from the Gira Server to Home Assistant.

**Password changed on the Gira side.**
The integration will detect the invalid token and start a reauth flow automatically — a notification will appear in Home Assistant prompting for new credentials.

## Support & Contribution

This is a community-developed integration and is **not officially supported by Gira**.

If you encounter bugs or have feature requests, please open an issue on GitHub. Pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Trademarks

Gira® and the Gira logo are trademarks of Gira Giersiepen GmbH & Co. KG.
This project is not affiliated with or endorsed by Gira.
