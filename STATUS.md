# Build status — Ethereum / Solidity / Ganache

## Active implementation

- Ethereum-compatible local ledger: Ganache
- Solidity governance and audit contract
- Hospital = local account 0
- per-researcher service identities map to distinct local accounts 1-9
- FastAPI Hospital token plus a validated multi-researcher token/wallet registry
- AES-256-GCM encrypted datasets in IPFS
- Hospital-local CID/SHA locator metadata with Ethereum commitment
- Microsoft SEAL 4.4 CKKS Average/SUM and DICOM statistics
- CT/MR DICOM ROI, slice, raw-voxel, block-statistics and DICOM SEG analysis

## Privacy / authorization hardening

- Dataset/request IDs are server-generated opaque values and Solidity rejects semantic/non-opaque identifiers.
- Dataset descriptions and research purposes are SHA-256 committed before immutable on-chain storage, and Solidity validates the commitment format.
- DICOM free-text Study/Series/Protocol descriptions are removed before encrypted storage.
- DICOM sanitization applies the PS3.15 2024b header rule table plus stricter free-text/private-tag/overlay cleanup and UID remapping.
- CT/MR upload fails closed unless BurnedInAnnotation=NO, RecognizableVisualFeatures=NO, and the Hospital supplies an explicit pixel-review attestation.
- Researcher plaintext dataset download is disabled by default; the normal access path is HE/compute-to-data.
- Upload plaintext, DICOM/CSV staging, SEAL plaintext inputs, and Hospital HE secret-key workspaces are confined to verified Linux tmpfs/ramfs.
- Researcher credential files and registry fail closed on broad POSIX permissions.
- Medical multipart and application plaintext staging is restricted to verified RAM-backed tmpfs/ramfs.
- Dataset AES keys are stored as AES-256-GCM-wrapped `MEDKEY01` blobs; the wrapping key must be injected externally and is never persisted by the app.
- Successful upload/key rotation automatically refreshes an authenticated, chain-bound recovery bundle containing wrapped keys and private locators.
- Each researcher token maps to its own Ethereum wallet; cross-researcher grant/download/HE use is rejected.
- DICOM SEG analysis requires independent authorization for both the source image dataset and SEG dataset.
- HE compute and Hospital decryption re-check all required active grants and dataset consent.

## Important boundaries

This remains a research prototype:
- one privileged Hospital wallet
- per-researcher service-token/wallet identities are implemented, but institutional OIDC/SSO and lifecycle provisioning are not
- local Ganache rather than a public/consortium production network
- dataset key blobs are master-key wrapped locally, but production KMS/HSM integration is still a future hardening step
- header de-identification follows the PS3.15 2024b rule table, but full clinical de-identification/compliance still requires appropriate pixel review and governance
- key rotation does not retroactively re-encrypt existing IPFS ciphertext

## Validation

GitHub Actions runs Solidity, Python adapter, IPFS, Microsoft SEAL, DICOM regression and full FastAPI E2E tests on every PR/push to main. The active DICOM work is tracked by PR #1.
