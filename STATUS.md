# Prototype status

The decentralized demo uses four Besu QBFT validators and three connected IPFS/Kubo peers. Ganache remains a faster Solidity and FastAPI compatibility test environment. Hospital governance, consent, researcher decisions, and final HE decryption stay under Hospital control.

Researchers authenticate with individual API tokens bound to their own external Ethereum wallet addresses. MetaMask/EIP-1193 signs EIP-191 digests for access requests and CSV/DICOM HE computations. The backend relays transactions with the Hospital gas account and never holds researcher private keys. Solidity verifies signers, action-specific chain/contract/request/job digests, and canonical ECDSA signatures.

Medical data is stored only as AES-256-GCM MEDAES ciphertext in IPFS. Ethereum stores opaque IDs, public data categories, commitments, and hashed HE descriptors. Legacy plaintext IPFS reads are rejected. CT/MR visual-PHI uploads require fail-closed attestation, DICOM headers are de-identified under PS3.15 2024b rules, and DICOM SEG requires dual authorization.

Recovery uses a chain-bound authenticated bundle for wrapped AES keys, private CID/SHA locators and the encrypted-object backup manifest. MEDAES object backups live in a separate configured directory. Restore verifies object SHA, CID and ledger commitment and republishes missing ciphertext to the replicated IPFS layer.

Hospital key-compromise remediation replaces the active encrypted object under a fresh key generation, with a staged backup, on-chain commitment update, retry journal and old-pin retirement. Deliberate recovery migration to another contract verifies the named source deployment and records destination provenance; ordinary restore remains chain-bound.

`scripts/setup_decentralized_demo.sh` starts or restarts the demo network. `scripts/status_decentralized_demo.sh` verifies node identities and shared state; `scripts/test_decentralized_topology.py` tests validator and IPFS outages plus full stop/restart persistence. `scripts/stop_decentralized_demo.sh` stops containers while retaining mounted node data.

This is a controlled research prototype, not a clinical compliance or production key-management system. Only synthetic or properly de-identified data should be used.
