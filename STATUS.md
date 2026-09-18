# Build status — Ethereum / Solidity / Ganache migration

## Active implementation

- Ethereum-compatible local ledger: Ganache
- Solidity governance contract
- 100 test ETH per local Ganache account
- Hospital = local account 0
- Researcher = local account 1
- FastAPI service-token roles preserved
- AES-256-GCM encrypted datasets preserved
- IPFS encrypted storage preserved
- Microsoft SEAL CKKS Average preserved
- Microsoft SEAL CKKS SUM preserved
- DICOM utilities preserved

## Fabric replacement

The active backend no longer invokes Fabric peer CLI commands. Existing API endpoints call `backend.ethereum_ledger`, which translates the previous governance operations into Solidity transactions and queries.

Because Ethereum does not provide Fabric implicit private collections, the encrypted object's CID and SHA-256 are kept in a Hospital-local private metadata store. Solidity stores only a cryptographic commitment to that locator.

## Automated validation

`.github/workflows/ethereum-e2e.yml` runs:

1. Ganache boot with 100 test ETH/account
2. Solidity compile
3. contract deployment
4. Solidity contract E2E
5. Python Ethereum adapter E2E
6. local IPFS
7. Microsoft SEAL 4.4 build
8. project HE binary build
9. DICOM regression
10. full FastAPI + Ganache + IPFS + AES + HE Average/SUM E2E

See the latest workflow run on the migration branch for the authoritative PASS/FAIL result.
