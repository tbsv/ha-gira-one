# Gira One Integration for Home Assistant

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

This integration connects Home Assistant to a Gira One Server via the local REST API. It allows you to control and monitor Gira devices directly within Home Assistant and receives real-time status updates via callbacks (Local Push).

## Features

This integration supports the following device platforms:

*   **Light**:
    *   Turn on/off
    *   Set brightness (dimming)
    *   Adjust color temperature (for Tunable White lights)
    *   Set color (for RGB/W lights)
*   **Cover**:
    *   Open, close, and stop
    *   Set a specific position
    *   Set slat position (tilt)
*   **Climate**:
    *   Read the current room temperature
    *   Set the target temperature
    *   Display the heating/cooling mode
    *   Read the current preset mode
    *   Set preset modes (e.g. Comfort, Eco, Away, Protection)

## Prerequisites

1.  A **Gira One Server** accessible on your local network.
2.  A **user account** on the Gira One Server with the necessary permissions to control the devices.
3.  **Home Assistant must have an external URL configured with SSL** (e.g., `https://example.duckdns.org`). This is mandatory for the Gira Server to send status changes (e.g., when a light is switched manually) back to Home Assistant.

## Installation (Recommended via HACS)

1.  Ensure you have HACS (Home Assistant Community Store) installed.
2.  In HACS, go to "Integrations".
3.  Click the three dots in the top right corner and select "Custom repositories".
4.  Paste the URL of this GitHub repository into the "Repository" field.
5.  Select "Integration" as the category.
6.  Click "Add".
7.  The "Gira One" integration will now appear in the list. Click "Install".
8.  Restart Home Assistant when prompted.

## Configuration

After installation, the integration is configured via the UI:

1.  Go to **Settings** > **Devices & Services**.
2.  Click the **"+ Add Integration"** button in the bottom right.
3.  Search for "Gira One" and select the integration.
4.  In the configuration dialog, enter the following details:
    *   **Host**: The IP address or hostname of your Gira One Server.
    *   **Username**: The username for your Gira One account.
    *   **Password**: The password for your Gira One account.
5.  Click "Submit".

The integration will now connect to your Gira One Server and automatically add all compatible devices to Home Assistant.

## Support & Contribution

This is a community-developed integration and is not officially supported by Gira.

If you encounter any issues or have a feature request, please create an issue on GitHub.