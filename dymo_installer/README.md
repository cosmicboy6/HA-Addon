# DYMO LabelWriter 450 Installer

A Home Assistant app that **builds and flashes the ESP32-S3 firmware** for the
[DYMO LabelWriter 450 print server](https://github.com/cosmicboy6/esp32-s3-dymo450-printserver)
— directly from Home Assistant, over USB. No PC, no command line.

This app is dedicated to the DYMO LabelWriter 450. The HP LaserJet 1010 has its
own app: [HP1010 Print Server Installer](../hp1010_installer).

## What it does

1. You enter your WiFi credentials and API key in the panel.
2. It compiles the ESPHome firmware (with the bundled `dymo_printserver`
   external component) inside the container.
3. It flashes the ESP32-S3 over USB with `esptool`, using a port from the host.

It also ships the prebuilt, no-secrets `dymo-firmware.factory.bin`, so you can do
a "quick flash" with no build at all — the board then boots a
**DYMO Print Fallback** access point where you set the WiFi.

## Requirements

- The ESP32-S3 plugged into the **Home Assistant host** with a *data* USB cable.
- The board must appear as a serial device (`/dev/ttyACM*` or `/dev/ttyUSB*`).
  The app requests `uart: true` and `usb: true` so both the native USB and the
  USB-UART bridge are passed through.
- The **DYMO LabelWriter 450** connected to the ESP32-S3's USB-OTG port (the
  ESP32 is the USB *host* to the printer).

## Usage

1. Open **DYMO 450 Installer** in the sidebar.
2. Fill in the WiFi SSID/password, the fallback AP name/password, and press
   **Generate** for the API key. Press **Save settings**.
3. Press **Build firmware**. The first build downloads the ESP32 toolchain and
   takes several minutes; later builds are cached in `/data` and are fast.
4. Plug in the board, pick the serial port, press **Flash built firmware**.
5. Add the device in Home Assistant — it is discovered over mDNS as
   `dymo-labelwriter-450` (use the API key shown in the panel). It also
   advertises itself on the network as a **DYMO LabelWriter 450** raw printer on
   TCP port 9100.

## Notes

- Everything the user types is stored only in the app's private `/data` volume.
  Nothing is uploaded; the GitHub repo and release images stay secret-free.
- The build cache (`/data/platformio`, `/data/idf`, `/data/project/.esphome`)
  lives on the persistent volume, so restarts and updates do not re-download.
- Pinned to ESPHome `2026.10.2`, matching the published firmware.

## Credits

- Inspired by [`mckinlk/esp32-zp450-print-server`](https://github.com/mckinlk/esp32-zp450-print-server).
- Built with **[OpenCode](https://opencode.ai)**, running in the
  [`sprisa/opencode`](https://hub.docker.com/r/sprisa/opencode) Docker image
  (source: [`sprisa/opencode-server`](https://github.com/sprisa/opencode-server)):

  ```sh
  docker pull sprisa/opencode:latest
  ```

- Built by **Big Pickle GO** 🥒
