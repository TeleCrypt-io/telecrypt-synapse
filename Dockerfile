# syntax=docker/dockerfile:1
# Build only in GitHub Actions. The server pulls an exact published tag; it never builds this image.
ARG SYNAPSE_VERSION=v1.158.0
FROM ghcr.io/element-hq/synapse:${SYNAPSE_VERSION} AS runtime

# The provider is separately maintained by matrix-org under Apache-2.0. Pin its released tag;
# upgrades are exercised against every candidate Synapse release before an image is published.
ARG S3_PROVIDER_VERSION=1.7.0
ARG CONTROLPLANE_RELEASE
USER root
RUN set -eux; \
    test -n "${CONTROLPLANE_RELEASE}" || { echo "CONTROLPLANE_RELEASE is required" >&2; exit 2; }; \
    wheel="telecrypt_tier_controller-${CONTROLPLANE_RELEASE}-py3-none-any.whl"; \
    release_url="https://github.com/TeleCrypt-io/controlplane/releases/download/${CONTROLPLANE_RELEASE}"; \
    curl --fail --location --silent --show-error --output "/tmp/${wheel}" "${release_url}/${wheel}"; \
    curl --fail --location --silent --show-error --output "/tmp/${wheel}.sha256" "${release_url}/${wheel}.sha256"; \
    cd /tmp; sha256sum --check "${wheel}.sha256"; \
    pip3 install --no-cache-dir \
      "https://github.com/matrix-org/synapse-s3-storage-provider/archive/refs/tags/v${S3_PROVIDER_VERSION}.tar.gz" \
      "/tmp/${wheel}"; \
    rm -f "/tmp/${wheel}" "/tmp/${wheel}.sha256"
COPY --chown=991:991 LICENSE THIRD_PARTY_NOTICES.md /licenses/
USER 991:991

FROM runtime AS test
RUN python -c "from tier_controller import TierController; import s3_storage_provider" && \
    python -c "from synapse.module_api import ModuleApi"

FROM runtime AS production
