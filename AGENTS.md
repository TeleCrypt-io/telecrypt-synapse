# Repository rules

1. This repository is an image builder only. It must consume the tier-controller from an exact
   external `controlplane` GitHub Release wheel and must not contain a copy of its source or tests.
2. TeleCrypt-authored source is BUSL-1.1. Preserve all third-party license notices.
3. Use exact release versions only: no `latest`, floating tags, or mutable deployment references.
4. Build and publish images only in GitHub Actions. Never build them locally or on a TeleCrypt VM.
5. A published image is never an authorization to deploy. Deployment requires a separate immutable
   `server-state-*` release and local Harness acceptance.
6. Never put credentials, buckets, endpoints that are not public, or production data in this repo.
