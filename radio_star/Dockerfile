ARG ARCH
FROM ghcr.io/home-assistant/${ARCH}-base-python:latest

LABEL \
    org.opencontainers.image.title="Radio-Star" \
    org.opencontainers.image.description="Sonos-FM-Aggregator: Timer, Automationen, TTS, MP3-Bibliothek & Timeline" \
    org.opencontainers.image.url="https://github.com/cosmicboy/radio-star" \
    io.hass.arch="${ARCH}" \
    io.hass.type="addon" \
    io.hass.version="1.0.0"

RUN pip install --no-cache-dir pyyaml

COPY radio_star/ /opt/radio_star/
COPY etc/services.d /etc/services.d
RUN chmod +x /etc/services.d/radio-star/run
