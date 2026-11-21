# hidee.net: the product site, the download, and the online demo (try.hidee.net). Real use runs locally with
# `hidee app`. The site runs from this source tree, so a site fix ships without a new release. The download serves
# ./release exactly as scripts/build_release.sh built and signed it (a rebuilt wheel would differ byte for byte and
# fail its signature).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY release/ /app/release/
COPY pyproject.toml README.md LICENSE NOTICE /app/src/
COPY hidee/ /app/src/hidee/
RUN pip install --no-cache-dir /app/src && rm -rf /app/src
RUN useradd --create-home hidee
USER hidee
ENV HIDEE_RELEASE_DIR=/app/release
CMD ["hidee", "web", "--public"]
