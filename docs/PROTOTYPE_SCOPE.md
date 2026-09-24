# Current prototype scope

## Implemented

- Solidity governance contract on local Ganache
- AES-256-GCM encrypted-at-rest dataset storage
- IPFS encrypted object storage
- private local CID/SHA metadata with on-chain commitment
- consent/access request approve/reject/revoke
- key-generation rotation audit
- HTTPS localhost FastAPI with Hospital/Researcher service tokens
- CSV numeric HE Average/SUM using Microsoft SEAL CKKS
- DICOM CT/MR sanitization and encrypted storage
- DICOM whole-volume, slice and ROI-box CKKS statistics
- DICOM SEG mask-based analysis with separate source+SEG authorization
- automated Solidity/adapter/DICOM/full-backend E2E CI

## Explicit prototype limitations

- synthetic or properly governed/de-identified data only
- no HIPAA/GDPR/DPDP compliance claim
- no full DICOM PS3.15 confidentiality-profile claim
- no KMS/HSM
- no per-user OIDC
- no multi-hospital validator/consortium deployment
- no mainnet/public-chain deployment
- key rotation is not historical ciphertext re-encryption
- approved raw download still exposes plaintext to the authorized researcher

## Next production-research directions

- per-person institutional identity and wallet binding
- multi-hospital/consortium governance
- KMS/HSM-backed key hierarchy and re-encryption workflow
- formal DICOM confidentiality-profile tooling/validation
- policy-bound model-to-data execution that avoids raw dataset download
