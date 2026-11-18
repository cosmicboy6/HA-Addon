# Print Server Installer (HP1010 / DYMO 450)

A Home Assistant app that **builds and flashes the ESP32-S3 firmware** for the
[ESP32-S3 print server](https://github.com/cosmicboy6/esp32-s3-hp1010-printserver)
— directly from Home Assistant, over USB. No PC, no command line.

Two targets are supported, chosen with the **Printer** dropdown in the panel:

| Printer | USB ID | Config |
|---|---|---|
| HP LaserJet 1010 | `03F0:0C17` | `hp1010-printserver.yaml` |
| DYMO LabelWriter 450 | `0922:0020` | `dymo-printserver.yaml` |

## What it does

1. You pick the printer, and enter your WiFi credentials and API key in the panel.
2. It compiles the matching ESPHome firmware (with the bundled
   `hp1010_printserver` external component) inside the container.
3. It flashes the ESP32-S3 over USB with `esptool`, using a port from the host.

It also ships the prebuilt, no-secrets factory images, so you can do a
"quick flash" with no build at all — the board then boots a fallback access
point (`HP1010 Print Fallback` / `hp1010setup`, or `DYMO Print Fallback` /
`dymosetup`) where you set the WiFi.

## Requirements

- The ESP32-S3 plugged into the **Home Assistant host** with a *data* USB cable.
- The board must appear as a serial device (`/dev/ttyACM*` or `/dev/ttyUSB*`).
  The app requests `uart: true` and `usb: true` so both the native USB and the
  USB-UART bridge are passed through.

## Usage

1. Open **Print Server Installer** in the sidebar.
2. Choose the **Printer** (HP LaserJet 1010 or DYMO LabelWriter 450).
3. Fill in the WiFi SSID/password, the fallback AP name/password, and press
   **Generate** for the API key. Press **Save settings**.
4. Press **Build firmware**. The first build downloads the ESP32 toolchain and
   takes several minutes; later builds are cached in `/data` and are fast.
5. Plug in the board, pick the serial port, press **Flash built firmware**.
6. Add the device in Home Assistant — it is discovered over mDNS as
   `hp1010-printserver` (HP) or `dymo-labelwriter-450` (DYMO). Use the API key
   shown in the panel.

> The **Printer** selection changes which firmware is built *and* which prebuilt
> image **Flash bundled prebuilt image** writes, so a quick flash always matches
> the selected printer.

## Notes

- Everything the user types is stored only in the app's private `/data` volume.
  Nothing is uploaded; the GitHub repo and release images stay secret-free.
- The build cache (`/data/platformio`, `/data/idf`, `/data/project/.esphome`)
  lives on the persistent volume, so restarts and updates do not re-download.
- Pinned to ESPHome `2026.10.2`, matching the published firmware.
- The DYMO config sets `page_count: false`: the LabelWriter has no PJL, so the
  HP page-count query would corrupt the label stream.

## Credits

- Built with **[OpenCode](https://opencode.ai)**, running in the
  [`sprisa/opencode`](https://hub.docker.com/r/sprisa/opencode) Docker image
  (source: [`sprisa/opencode-server`](https://github.com/sprisa/opencode-server)):

  ```sh
  docker pull sprisa/opencode:latest
  ```

- Built by **Big Pickle GO** 🥒
