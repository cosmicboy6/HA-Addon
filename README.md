# Home Assistant Add-ons


## About

Home Assistant lets anyone publish a repository of add-ons. This repository
provides extra add-ons for a Home Assistant installation — currently the
ESP32-S3 **print-server installers** that build and flash firmware over USB
straight from Home Assistant.

## Installation

In the Home Assistant add-on store, add this repository by URL:

```txt
https://github.com/cosmicboy6/HA-Addon
```

<a href="https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcosmicboy6%2FHA-Addon" target="_blank" rel="noreferrer noopener"><img src="https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg" alt="Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled." /></a>

## Add-ons provided by this repository

### [HP1010 Print Server Installer](https://github.com/cosmicboy6/HA-Addon/tree/main/hp1010_installer)

Build and flash the ESP32-S3 firmware for the HP LaserJet 1010 raw-9100 print
server, over USB, directly from Home Assistant.

### [DYMO LabelWriter 450 Installer](https://github.com/cosmicboy6/HA-Addon/tree/main/dymo_installer)

Build and flash the ESP32-S3 firmware for the DYMO LabelWriter 450 raw-9100
print server, over USB, directly from Home Assistant.

## Support

Got questions or found a problem? Open an issue on GitHub:

- https://github.com/cosmicboy6/HA-Addon/issues

Each add-on has its own firmware repository with more detail — see the add-on's
own README.

## Credits

- Built with **[OpenCode](https://opencode.ai)**, running in the
  [`sprisa/opencode`](https://hub.docker.com/r/sprisa/opencode) Docker image
  (source: [`sprisa/opencode-server`](https://github.com/sprisa/opencode-server)):

  ```sh
  docker pull sprisa/opencode:latest
  ```

- Built by **Big Pickle GO** 🥒

## License

MIT License

Copyright (c) 2024 Hans HAUBERGER

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

[![Sponsor this project](https://img.shields.io/badge/Sponsor%20this%20project-FFDD00?style=flat-square&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/hanshauberger)
