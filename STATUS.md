# Build status — Ethereum / Solidity / Ganache

## Active implementation

- Ethereum-compatible local ledger: Ganache
- Solidity governance and audit contract
- Hospital = local account 0
- Researcher = local account 1
- FastAPI service-token roles
- AES-256-GCM encrypted datasets in IPFS
- Hospital-local CID/SHA locator metadata with Ethereum commitment
- Microsoft SEAL 4.4 CKKS Average/SUM and DICOM statistics
- CT/MR DICOM ROI, slice, raw-voxel, block-statistics and DICOM SEG analysis

## Privacy / authorization hardening

- Dataset descriptions and research purposes are SHA-256 committed before immutable on-chain storage, and Solidity validates the commitment format.
- DICOM free-text Study/Series/Protocol descriptions are removed before encrypted storage.
- DICOM sanitization recursively removes configured identifiers, free-text risk fields, private tags and overlays, remaps identity UIDs, clears risky file-meta fields and zeros the Part 10 preamble.
- DICOM SEG analysis requires independent authorization for both the source image dataset and SEG dataset.
- HE compute and Hospital decryption re-check all required active grants and dataset consent.

## Important boundaries

This remains a research prototype:
- one privileged Hospital wallet and one mapped Researcher wallet
- service tokens instead of per-person OIDC/wallet identities
- local Ganache rather than a public/consortium production network
- local key files rather than KMS/HSM
- DICOM sanitization is not a full PS3.15 compliance implementation
- key rotation does not retroactively re-encrypt existing IPFS ciphertext

## Validation

GitHub Actions runs Solidity, Python adapter, IPFS, Microsoft SEAL, DICOM regression and full FastAPI E2E tests on every PR/push to main. The active DICOM work is tracked by PR #1.
