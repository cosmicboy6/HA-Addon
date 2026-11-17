# HP1010 Print Server Installer

A Home Assistant app that **builds and flashes the ESP32-S3 firmware** for the
[HP LaserJet 1010 print server](https://github.com/cosmicboy6/esp32-s3-hp1010-printserver)
— directly from Home Assistant, over USB. No PC, no command line.

## What it does

1. You enter your WiFi credentials and API key in the panel.
2. It compiles the ESPHome firmware (with the bundled `hp1010_printserver`
   external component) inside the container.
3. It flashes the ESP32-S3 over USB with `esptool`, using a port from the host.

It also ships the prebuilt, no-secrets `firmware.factory.bin`, so you can do a
"quick flash" with no build at all — the board then boots a
**HP1010 Print Fallback** access point where you set the WiFi.

## Requirements

- The ESP32-S3 plugged into the **Home Assistant host** with a *data* USB cable.
- The board must appear as a serial device (`/dev/ttyACM*` or `/dev/ttyUSB*`).
  The app requests `uart: true` and `usb: true` so both the native USB and the
  USB-UART bridge are passed through.

## Usage

1. Open **HP1010 Installer** in the sidebar.
2. Fill in the WiFi SSID/password, the fallback AP name/password, and press
   **Generate** for the API key. Press **Save settings**.
3. Press **Build firmware**. The first build downloads the ESP32 toolchain and
   takes several minutes; later builds are cached in `/data` and are fast.
4. Plug in the board, pick the serial port, press **Flash built firmware**.
5. Add the device in Home Assistant — it is discovered over mDNS as
   `hp1010-printserver` (use the API key shown in the panel).

## Notes

- Everything the user types is stored only in the app's private `/data` volume.
  Nothing is uploaded; the GitHub repo and release images stay secret-free.
- The build cache (`/data/platformio`, `/data/idf`, `/data/project/.esphome`)
  lives on the persistent volume, so restarts and updates do not re-download.
- Pinned to ESPHome `2026.10.2`, matching the published firmware.
