# syntax=docker/dockerfile:1
# Build only in GitHub Actions. The server pulls an exact published tag; it never builds this image.
ARG SYNAPSE_VERSION=v1.155.0
FROM ghcr.io/element-hq/synapse:${SYNAPSE_VERSION} AS runtime

# The provider is separately maintained by matrix-org under Apache-2.0. Pin its released tag;
# upgrades are exercised against every candidate Synapse release before an image is published.
ARG S3_PROVIDER_VERSION=1.6.1
USER root
RUN pip3 install --no-cache-dir \
    "https://github.com/matrix-org/synapse-s3-storage-provider/archive/refs/tags/v${S3_PROVIDER_VERSION}.tar.gz"

# This is the only TeleCrypt code running inside Synapse. It is source-baked into the immutable
# image rather than bind-mounted or installed during container startup.
COPY --chown=991:991 tier_controller /modules/tier_controller
COPY --chown=991:991 LICENSE THIRD_PARTY_NOTICES.md /licenses/
ENV PYTHONPATH=/modules
USER 991:991

FROM runtime AS test
USER root
RUN pip3 install --no-cache-dir pytest==9.0.2 pytest-asyncio==1.4.0
COPY --chown=991:991 tests /tests
USER 991:991
RUN python -c "from tier_controller import TierController; import s3_storage_provider" && \
    python -m pytest -q /tests

FROM runtime AS production
