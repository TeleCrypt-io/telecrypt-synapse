# Repository rules

1. TeleCrypt-authored source is BUSL-1.1. Preserve all third-party license notices.
2. Use exact release versions only: no `latest`, floating tags, or mutable deployment references.
3. Build and publish images only in GitHub Actions. Never build them locally or on a TeleCrypt VM.
4. A published image is never an authorization to deploy. Deployment requires a separate immutable
   `server` release and local Harness acceptance.
5. Never put credentials, buckets, endpoints that are not public, or production data in this repo.
