# Nest Legacy Integration for Home Assistant

![Nest Legacy Header](https://brands.home-assistant.io/nest/logo.png)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

This is a custom component for Home Assistant to integrate a wide range of Nest devices using an unofficial web API. It serves as an alternative to the official Nest integration, providing support for devices not available through Google's official Smart Device Management (SDM) API, such as the Nest Protect, Nest x Yale Lock, and Nest Heat Link.

This integration provides real-time updates for most sensors and controls by maintaining a persistent connection to the Nest API.

## Why use Nest Legacy?

The official Home Assistant Nest integration uses Google's Smart Device Management (SDM) API, which has a limited set of supported devices and requires a one-time $5 fee. This **Nest Legacy** integration uses the same unofficial API that the Nest mobile and web apps use, offering several key advantages.

### Comparison with the Official Nest Integration

| Feature                  | Nest Legacy (This Integration)                                  | Official Nest Integration (SDM API)                            |
| ------------------------ | --------------------------------------------------------------- | -------------------------------------------------------------- |
| **API Used**             | Unofficial Nest Web API                                         | Official Google SDM API                                        |
| **Cost**                 | Free                                                            | $5 USD one-time fee required by Google                         |
| **Authentication**       | Access Token (Nest Account) or Cookies (Google Account)         | OAuth2 with Google Cloud Project                               |
| **Supported Devices**    | Wider range, including **Nest Protect**, **Nest Temp Sensors**, **Nest x Yale Locks**, and **Nest Heat Link**. | Limited to newer Thermostats, Cameras, and Doorbells.         |
| **Stability**            | Relies on an unofficial API that could change or break without notice. | Officially supported by Google, more stable long-term.        |

**In short, use this integration if you want to:**

- Integrate Nest Protects, Temperature Sensors, Locks, or Heat Links.
- Avoid the $5 Google API fee.
- Access features not exposed by the official API.

## Supported Devices

This integration supports a wide variety of Nest devices:

- **Nest Thermostats** (1st, 2nd, 3rd gen, Thermostat E, 2020 mirror edition)
- **Nest Protect** (1st and 2nd gen, both wired and battery)
- **Nest Temperature Sensors**
- **Nest Cameras** (Cam Indoor, IQ Indoor, Outdoor, IQ Outdoor)
- **Nest Doorbells** (Wired 1st gen)
- **Nest x Yale Locks**
- **Nest Heat Link** (for UK/EU hot water control)

## Features

This integration creates a rich set of entities for your Nest devices.

- **Nest Thermostat**:
  - `climate` entity for full control over temperature and HVAC modes (Heat, Cool, Heat/Cool, Off).
  - `fan` entity to control the fan independently.
  - `number` entity to configure the fan timer duration.
  - `binary_sensor` for occupancy and Eco Mode status.
  - `sensor` for humidity and backplate temperature.

- **Nest Protect**:
  - `binary_sensor` entities for Smoke, Carbon Monoxide (CO), and Heat alarms.
  - `binary_sensor` for occupancy (wired models only).
  - `binary_sensor` entities for diagnostic status (battery health, line power, component tests).
  - `sensor` entities for battery level and replacement date.
  - `switch` entities to control Pathlight, Nightly Promise, Heads-Up alerts, and Steam Check.
  - `select` entity to adjust the Pathlight brightness.

- **Nest Temperature Sensor**:
  - `sensor` for temperature and battery level.

- **Nest Cameras & Doorbells**:
  - `camera` entity with live streaming.
  - `switch` entities to enable/disable streaming, audio, night vision, and the status LED.
  - `switch` for Indoor Chime and Visitor Announcements (Doorbells only).
  - `event` entities that fire for doorbell presses, motion, person, sound, and face detection.

- **Camera Event Media Browser**:
  - A `media_source` is provided, allowing you to browse, view, and download recent camera event clips and thumbnails directly from the Home Assistant Media Browser.

- **Nest x Yale Lock**:
  - `lock` entity to lock and unlock.
  - `sensor` to show who performed the last lock/unlock action (keypad, manual, remote).
  - `binary_sensor` for tamper detection.
  - `switch` to enable/disable auto-relock.
  - `number` entity to configure the auto-relock duration.

- **Nest Heat Link**:
  - `water_heater` entity for full control over your hot water system, including setting temperature, mode, and activating a boost.

- **Nest Home/Structure**:
  - `select` entity to set the home's mode (Home, Away, Vacation).

## Installation

## HACS

1. [Add](http://homeassistant.local:8123/hacs/dashboard) custom integrations repository: `https://github.com/tronikos/nest_legacy`
2. Select "Nest Legacy" in the Integration tab and click download
3. Restart Home Assistant
4. Enable the integration

## Configuration

After installation, the integration can be configured via the Home Assistant UI.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=nest_legacy)

You will be asked to select your account type. Follow the instructions below based on your account.

### Google Account Configuration

For accounts migrated to Google, or created after August 2019. You will need to retrieve an `issue_token` and `cookies` from your browser.

(Instructions adapted from the excellent `homebridge-nest` project).

1. Open a Chrome browser tab in **Incognito Mode**.
2. Open Developer Tools (View > Developer > Developer Tools, or `Ctrl+Shift+I` / `Cmd+Option+I`).
3. Click on the **Network** tab. Make sure **Preserve Log** is checked.
4. In the 'Filter' box, enter `issueToken`.
5. Go to `home.nest.com`, and click **Sign in with Google**. Log into your account.
6. One network call (beginning with `iframerpc`) will appear in the Dev Tools window. Click on it.
7. In the **Headers** tab, under **General**, copy the entire **Request URL**. This is your `Issue token`.
8. Clear the filter box and now enter `oauth2/iframe`.
9. Several network calls will appear. Click on the **last `iframe` call**.
10. In the **Headers** tab, under **Request Headers**, find the `cookie` entry. Copy the **entire cookie string** (it will be very long). This is your `Cookies`.
11. Paste these values into the Home Assistant configuration form.
12. **Do not log out of `home.nest.com`**, as this will invalidate your credentials. Just close the browser tab.

### Legacy Nest Account Configuration

For older, non-migrated Nest accounts. You will need to obtain an `access_token`.

1. Go to `https://home.nest.com` in your browser and log in.
2. Once logged in, open a new tab and go to `https://home.nest.com/session`.
3. You will see a long string of text. Find `"access_token": "..."` near the beginning.
4. Copy the value inside the quotes (it's a long sequence of letters, numbers and punctuation beginning with `b`). This is your `Access token`.
5. Paste this value into the Home Assistant configuration form.
6. **Do not log out of `home.nest.com`**, as this will invalidate your credentials. Just close the browser tab.

### Field Test Environment

If you are part of the Google Field Test program, check the "Use Field Test environment" box during setup.

## Credits

This integration would not be possible without the extensive research and work done by these projects:

- <https://github.com/chrisjshull/homebridge-nest>
- <https://github.com/n0rt0nthec4t/homebridge-nest-accfactory>
- <https://github.com/iMicknl/ha-nest-protect>

## Screenshots

### Configuration

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/92bf01b3-98b6-4a01-9e6e-5e6e22dc1ca7" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/cae9c9ea-317e-4823-b7d0-744982d51ee4" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/5ad4ed27-85e2-4eca-b892-5c4f13f56aef" />

### Nest Protect

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/e1e42e58-78be-4d26-b8aa-08a124703d58" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/571b9e65-c6c7-422c-8482-e3c8a3319992" />

### Nest Camera

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/d353b7de-c538-4fa6-bb0c-736377cd8419" />

### Nest Doorbell

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/eb2c978f-ba9c-4c75-a726-b5db7e2660ae" />

### Nest Lock

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/c7e248b7-be6f-479f-9475-b70f67ea1939" />

## Home

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/024763eb-bd52-4810-a4b7-8bcae33a1d73" />

## Media Browser

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/6dd41a07-c59b-485c-8c86-681e561f50cc" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/b7acb8c7-9b5e-47ca-afd2-90b6034daa60" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/967406e1-42cf-4cf9-bf1b-bd6d7e4ba350" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/87f2d922-959d-4007-b715-3bd1aacb935b" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/7955d2eb-c72b-485b-96e6-b05772b73970" />

## Disclaimer

This is a personal hobby project and is not affiliated with Google or Nest. It uses an unofficial API that could be changed or discontinued by Google at any time, which may cause this integration to stop working. It is provided "as-is," with no warranty whatsoever. Use at your own risk.
