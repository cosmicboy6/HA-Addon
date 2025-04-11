ARG BUILD_FROM
FROM $BUILD_FROM

# Install requirements for add-on
RUN \

 docker run \
  -d \
  -p 52050:52050 \
  -p 52051:52051 \
  -v /mnt/data/supervisor/media:/medialibrary \
  -v /mnt/data/supervisor/config/datadir:/datadir bizmodeller/mymediaforalexa \	
	
# Python 3 HTTP Server serves the current working dir
# So let's set it to our add-on persistent data directory.
WORKDIR /data

# Copy data for add-on
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

# Labels
LABEL \
    io.hass.name="MymediaforAlexa" \
    io.hass.description="Stream your Home Assistant Media Share music collection to your Amazon Echo or Amazon Dot. No Cloud." \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="hans HAUBERGER<hans.hauberger@gmail.com>" \
    org.label-schema.description="Stream your Home Assistant Media Share music collection to your Amazon Echo or Amazon Dot. No Cloud." \
    org.label-schema.build-date=${BUILD_DATE} \
    org.label-schema.name="MymediaforAlexa" \
    org.label-schema.schema-version="1.0" \
    org.label-schema.url="https://addons.community" \
    org.label-schema.usage="https://github.com/cosmicboy6/HA_ADDON/tree/master/README.md" \
    org.label-schema.vcs-ref=${BUILD_REF} \
    org.label-schema.vcs-url="https://github.com/cosmicboy6/HA_ADDON/tree/master/mymediaforalexa" \
    org.label-schema.vendor="Community Hass.io Addons"