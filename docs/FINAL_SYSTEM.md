# Medical Research Prototype — Current System

## Data path

Hospital upload
1. validate/sanitize identifiers
2. DICOM prototype sanitization when applicable
3. AES-256-GCM encryption
4. encrypted object stored in IPFS
5. CID + encrypted-object SHA-256 retained in Hospital-local private metadata
6. Ethereum stores only a locator commitment plus non-sensitive governance metadata

Research access
1. Researcher creates a request; free-text purpose is SHA-256 committed before Ethereum
2. Hospital approves/rejects on Ethereum
3. backend checks active consent and request state
4. approved plaintext download is possible for the authorized researcher

Homomorphic-encryption path
1. Hospital derives numeric values and creates Microsoft SEAL CKKS ciphertext
2. ciphertext artifacts are stored in IPFS
3. Ethereum records HE provenance
4. Researcher computes without the HE secret key
5. encrypted result is stored in IPFS
6. Hospital re-checks authorization immediately before decrypting
7. Ethereum records the completed transition

DICOM SEG jobs require **two independently approved grants**: one for the source CT/MR dataset and one for the SEG dataset. Both requests must map to the same researcher.

## Ethereum stores

- dataset ID, type, owner wallet, consent/storage state
- SHA-256 commitment of arbitrary user-supplied dataset description (non-DICOM)
- structural DICOM discovery metadata only
- access request ID and SHA-256 purpose commitment
- access decisions
- locator commitment
- key-rotation audit records
- HE job state/provenance
- optional secondary dataset/request provenance for multi-dataset HE jobs

## Ethereum does not store

- raw medical bytes
- patient identifiers
- IPFS CID for the encrypted dataset
- dataset AES keys
- HE secret keys
- plaintext research purpose
- arbitrary plaintext dataset description
- decrypted aggregate

## Security boundaries

- AES keys and private dataset locator metadata remain Hospital-local.
- Existing ciphertext keeps the key generation used at encryption time. Rotation creates a new active generation but does not rewrite old IPFS objects.
- Service-token identities and the single-Hospital/single-Researcher local wallet mapping are prototype constraints, not a production decentralization model.
- DICOM sanitization is not a formal HIPAA/GDPR/DPDP or DICOM PS3.15 compliance claim.
