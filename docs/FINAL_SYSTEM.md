# Final Medical Research Prototype — Ethereum Migration

Active architecture:
- Solidity smart contract on local Ganache
- Ganache development accounts funded with 100 test ETH each
- AES-256-GCM encrypted medical data in IPFS
- Hospital-local private CID/SHA metadata
- Ethereum locator commitment for tamper detection
- Hospital/researcher service-token authentication
- HTTPS on localhost:8443
- consent-based research access
- Microsoft SEAL CKKS encrypted Average and SUM
- researcher computation without the HE secret key
- on-chain dataset, access and HE workflow audit histories

What Ethereum stores:
- dataset discovery metadata
- owner wallet
- consent state
- storage-ready state
- private-locator commitment
- access requests and decisions
- key-rotation audit records
- HE job hashes/CIDs and workflow states

What Ethereum never stores:
- raw medical files
- plaintext medical values
- AES dataset keys
- Microsoft SEAL secret keys
- decrypted aggregates
- the Hospital's private locator file

Important limitations:
- research prototype, not production clinical infrastructure
- no HIPAA/GDPR/DPDP compliance claim
- local Ganache, not a public/mainnet deployment
- service tokens instead of per-user OIDC identities
- local key material instead of KMS/HSM
- DICOM de-identification is not a complete PS3.15 implementation
- HE currently targets numeric CSV statistics
- approved download still exposes plaintext to an authorized researcher

Research direction:
Privacy-preserving decentralized biomedical research using encrypted off-chain storage, Ethereum governance, private metadata commitments, auditable consent/access and model-to-data computation.
