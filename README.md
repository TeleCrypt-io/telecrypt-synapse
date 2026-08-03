# TeleCrypt Synapse

Public, reproducible Synapse image for TeleCrypt.

```text
official Synapse release
  + matrix-org S3 storage provider
  + exact external TeleCrypt tier-controller release
  = ghcr.io/telecrypt-io/telecrypt-synapse:<version>
```

This repository does not fork or patch Synapse. It uses the official exact Synapse image and its
supported Python module interface. The TeleCrypt module limits unverified users' uploads, encrypted
room creation, encryption events, and room count; only Synapse users with `user_type: verified` are
unrestricted.

## Image versions

An image tag has the form `<synapse-version>-cp.<controlplane-release>-s3.<provider-version>-tc.<revision>`,
for example `1.155.0-cp.0.3.6-s3.1.6.1-tc.1`.

- `<synapse-version>` is the upstream Element Synapse release.
- `cp.<controlplane-release>` and `s3.<provider-version>` are exact independently released components.
- `tc.<revision>` identifies an immutable builder revision for that exact component tuple. It is
  never overwritten.

Images are built only by this repository's GitHub Actions workflows. A scheduled workflow detects a
new stable upstream Synapse release, builds it with pinned external releases, runs the smoke test,
generates provenance, and publishes only a passing image. The policy unit suite belongs to the
independently versioned module repository. It never deploys to TeleCrypt infrastructure. A failed
candidate is not published.

`server` is responsible only for selecting a tested exact image tag in a separately released
configuration change. The Linux VM must never build or install Python packages at runtime.

## Components

- Base: `ghcr.io/element-hq/synapse:v<version>`.
- Media provider: [`matrix-org/synapse-s3-storage-provider`](https://github.com/matrix-org/synapse-s3-storage-provider), Apache-2.0, pinned in the Dockerfile.
- Policy: [`TeleCrypt-io/controlplane`](https://github.com/TeleCrypt-io/controlplane), installed as a
  `telecrypt_tier_controller` wheel from the exact public GitHub Release. Its accompanying
  `.sha256` release asset is verified during the image build.

The provider is configured by Synapse's `media_storage_providers` setting; this image contains no
S3 endpoint, bucket, or credentials. Those remain server-only secrets.

## Release and deployment boundary

An automatically published image is an available, tested artifact—not a deployment. To adopt one:

1. create a reviewed immutable `server` release referencing its exact tag;
2. verify the release through local Harness acceptance; and
3. deploy that `server` release explicitly.

Never use `latest`, a floating Synapse tag, a bind-mounted Python module, or a runtime `pip install`.

## Licensing

TeleCrypt-authored source is BUSL-1.1. The image also includes third-party components under their
own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
