# Medical Research Prototype — Current System

## Data path

Hospital upload
1. multipart and application plaintext stage only on verified RAM-backed tmpfs/ramfs
2. assign opaque dataset identifiers and validate the upload
3. DICOM sanitization when applicable
4. AES-256-GCM encryption
5. encrypted object replicated and pinned across three IPFS peers in the decentralized demo
6. CID + encrypted-object SHA-256 retained in Hospital-local private metadata
7. Ethereum stores only a locator commitment plus non-sensitive governance metadata
8. encrypted recovery snapshot is refreshed at the configured recovery destination

Research access
1. each Researcher authenticates with an individual service token mapped to a distinct Ethereum wallet
2. Researcher creates a request signed by that wallet; free-text purpose is SHA-256 committed before Ethereum
3. Hospital approves/rejects on Ethereum
4. backend checks active consent, request state, and authenticated researcher-wallet ownership
5. the default policy is compute-to-data/HE; plaintext download is blocked
6. a controlled local demo may explicitly opt in with `MEDICAL_ALLOW_PLAINTEXT_DOWNLOADS=true`, but only the researcher that owns the approved request can use it

Homomorphic-encryption path
1. Hospital derives numeric values and creates Microsoft SEAL CKKS ciphertext
2. ciphertext artifacts are stored in IPFS
3. Ethereum records HE provenance and SHA-256 artifact-locator commitments; private CIDs stay in the Hospital metadata store
4. Researcher computes without the HE secret key
5. encrypted result is stored in IPFS
6. Hospital re-checks authorization immediately before decrypting
7. Ethereum records the completed transition

DICOM SEG jobs require **two independently approved grants**: one for the source CT/MR dataset and one for the SEG dataset. Both requests must map to the same researcher.

## Ethereum stores

- opaque server-issued dataset ID, type, owner wallet, consent/storage state
- SHA-256 commitment of the submitted dataset description
- opaque server-issued access request ID and SHA-256 purpose commitment
- access decisions
- locator commitment
- key-rotation audit records
- HE job state/provenance
- SHA-256 commitments of HE ciphertext/result CIDs, never the raw CIDs
- optional secondary dataset/request provenance for multi-dataset HE jobs

## Ethereum does not store

- raw medical bytes
- patient identifiers
- IPFS CID for the encrypted dataset
- IPFS CIDs for HE artifacts
- dataset AES keys
- HE secret keys
- plaintext research purpose
- arbitrary plaintext dataset description
- decrypted aggregate

## Identifier privacy

FastAPI generates `ds-<32 hex>` and `req-<32 hex>` identifiers. Caller-provided dataset labels and request labels are not written to Ethereum. Solidity independently rejects IDs that do not match the opaque formats.

## Security boundaries

- dataset AES keys remain Hospital-local only as AES-256-GCM-wrapped `MEDKEY01` blobs; the wrapping key is supplied externally through `MEDICAL_MASTER_KEY_HEX` and is never written by the application.
- private dataset locator metadata remains Hospital-local during normal operation and is included only inside the authenticated encrypted recovery bundle.
- plaintext medical upload staging is restricted to verified tmpfs/ramfs and never relies on ordinary-disk deletion for erasure.
- successful dataset registration and key rotation refresh a chain-bound encrypted recovery bundle. The private HE locator map is included in a snapshot. Restore verifies key metadata, encrypted-object SHA/CID, and private dataset-locator commitments against Ethereum transactionally, then restores missing MEDAES objects to replicated IPFS.
- Existing ciphertext keeps the key generation used at encryption time. Rotation creates a new active generation but does not rewrite old IPFS objects.
- For suspected key compromise, Hospital remediation re-encrypts the active object under a fresh key, verifies it, updates the ledger commitment and recovery snapshot, then unpins the old object. A journal supports retry after an interrupted finalization.
- Ordinary recovery rejects a different chain or contract. Explicit Hospital migration verifies source commitments and authority, registers destination provenance, and restores encrypted backups without a plaintext export. Requests and signatures must be recreated.
- The Hospital remains a single privileged local wallet. Researchers have individual service-token identities bound to externally owned wallets; the backend holds no researcher private keys. Institutional OIDC/SSO is outside this prototype.
- DICOM sanitization is not a formal HIPAA/GDPR/DPDP compliance claim.
- CT/MR upload is fail-closed unless BurnedInAnnotation and RecognizableVisualFeatures are both `NO` and the Hospital explicitly attests that pixel data was visually reviewed.
- Plaintext researcher release is disabled by default and is a deliberate demo-only opt-in.
- The decentralized demo uses four Besu QBFT validators and three IPFS peers. Ganache is retained only for fast compatibility CI. CKKS output is approximate; `RAW_VOXELS` and `BLOCK_STATS` report their distinct representations.
