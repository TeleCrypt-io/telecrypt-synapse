# Third-party notices

This project packages, but does not modify, the following third-party components:

- [Synapse](https://github.com/element-hq/synapse), supplied by its official container image at
  the exact version recorded in `versions.env` and licensed by Element under its own terms.
- [synapse-s3-storage-provider](https://github.com/matrix-org/synapse-s3-storage-provider),
  Apache License 2.0, pinned to the exact released version in `versions.env`.
- [controlplane tier controller](https://github.com/TeleCrypt-io/controlplane), TeleCrypt BUSL-1.1
  code, installed from the exact public release recorded in `versions.env`.

The published image must retain all notices and license obligations from these dependencies.
