# TeleCrypt Synapse

Public, reproducible Synapse image for TeleCrypt.

```text
official Synapse release
  + matrix-org S3 storage provider
  + TeleCrypt tier-controller
  = ghcr.io/telecrypt-io/telecrypt-synapse:<version>
```

This repository does not fork or patch Synapse. It uses the official exact Synapse image and its
supported Python module interface. The TeleCrypt module limits unverified users' uploads, encrypted
room creation, encryption events, and room count; only Synapse users with `user_type: verified` are
unrestricted.

## Image versions

An image tag has the form `<synapse-version>-tc.<revision>`, for example `1.155.0-tc.1`.

- `<synapse-version>` is the upstream Element Synapse release.
- `tc.<revision>` identifies the immutable TeleCrypt policy/provider build for that upstream
  version. It is never overwritten.

Images are built only by this repository's GitHub Actions workflows. A scheduled workflow detects a
new stable upstream Synapse release, builds it with the pinned provider, runs the policy/provider
smoke and unit tests, generates provenance, and publishes only a passing image. It never deploys to
TeleCrypt infrastructure. A failed candidate is not published.

`server` is responsible only for selecting a tested exact image tag in a separately released
configuration change. The Linux VM must never build or install Python packages at runtime.

## Components

- Base: `ghcr.io/element-hq/synapse:v<version>`.
- Media provider: [`matrix-org/synapse-s3-storage-provider`](https://github.com/matrix-org/synapse-s3-storage-provider), Apache-2.0, pinned in the Dockerfile.
- Policy: [`tier_controller/`](tier_controller/), TeleCrypt BUSL-1.1 code.

The provider is configured by Synapse's `media_storage_providers` setting; this image contains no
S3 endpoint, bucket, or credentials. Those remain server-only secrets.

## Release and deployment boundary

An automatically published image is an available, tested artifact—not a deployment. To adopt one:

1. create a reviewed immutable `server` release referencing its exact tag;
2. verify the release through local Harness acceptance; and
3. deploy that `server` release explicitly, with the normal rollback record.

Never use `latest`, a floating Synapse tag, a bind-mounted Python module, or a runtime `pip install`.

## Licensing

TeleCrypt-authored source is BUSL-1.1. The image also includes third-party components under their
own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
