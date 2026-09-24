# Current prototype scope

## Implemented

- Solidity governance contract on a four-validator Besu QBFT demo network (Ganache for fast CI compatibility)
- AES-256-GCM encrypted-at-rest dataset storage
- encrypted object and HE artifact storage pinned across three IPFS peers
- private local CID/SHA metadata with on-chain commitment
- opaque server-issued dataset/request IDs enforced by Solidity
- AES-256-GCM-wrapped dataset-key files with an externally supplied master wrapping key
- verified RAM-backed tmpfs/ramfs staging for medical plaintext, including multipart spooling
- automatic AES-GCM-authenticated recovery snapshots for wrapped keys, private locators and encrypted-object manifests
- chain-bound recovery restore with encrypted-object SHA/CID and Ethereum locator verification
- distinct researcher service identities bound to externally owned Ethereum wallets and EIP-191 signatures
- consent/access request approve/reject/revoke
- key-generation rotation audit
- HTTPS localhost FastAPI with a Hospital token and per-researcher token-to-public-address registry
- CSV numeric HE Average/SUM using Microsoft SEAL CKKS
- DICOM CT/MR sanitization and encrypted storage
- DICOM whole-volume, slice and ROI-box CKKS statistics
- DICOM SEG mask-based analysis with separate source+SEG authorization
- automated Solidity/adapter/DICOM/full-backend E2E CI

## Explicit prototype limitations

- synthetic or properly governed/de-identified data only
- no HIPAA/GDPR/DPDP compliance claim
- PS3.15 2024b header rules are applied, but full pixel/clinical confidentiality-profile compliance is not claimed
- no production KMS/HSM integration yet; the prototype uses externally supplied master-key wrapping
- no institutional OIDC/SSO yet; current per-researcher identities use service tokens
- no multi-hospital validator/consortium deployment
- no mainnet/public-chain deployment
- key rotation is not historical ciphertext re-encryption
- plaintext researcher download is disabled by default; a controlled local demo can explicitly opt in

## Next production-research directions

- replace service-token researcher identities with institutional OIDC/SSO lifecycle provisioning
- multi-hospital/consortium governance
- KMS/HSM-backed key hierarchy and re-encryption workflow
- formal DICOM confidentiality-profile tooling/validation
- expand policy-bound compute-to-data beyond the current HE statistics
