ARG BUILD_ARCH
# Debian (not Alpine): ESPHome's platformio / ESP-IDF toolchains need glibc.
FROM ghcr.io/home-assistant/base-debian:trixie

LABEL \
    org.opencontainers.image.title="DYMO LabelWriter 450 Installer" \
    org.opencontainers.image.description="Build and flash the ESP32-S3 DYMO LabelWriter 450 print-server firmware over USB" \
    org.opencontainers.image.url="https://github.com/cosmicboy6/HA-Addon/tree/main/dymo_installer" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION:-1.0.0}"

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip python3-dev git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir "esphome==2026.10.2" esptool \
 && /opt/venv/bin/platformio settings set enable_telemetry No \
 && /opt/venv/bin/platformio settings set check_platformio_interval 1000000

ENV PATH="/opt/venv/bin:${PATH}"

COPY dymo_installer/ /opt/dymo_installer/
COPY etc/services.d /etc/services.d
RUN chmod +x /etc/services.d/dymo_installer/run
