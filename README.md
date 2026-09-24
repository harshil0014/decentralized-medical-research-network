# Decentralized Medical Research Network

Local research prototype for privacy-preserving medical-data sharing and encrypted analytics.

## Current blockchain stack

The active governance layer is:

- **Ethereum-compatible local blockchain**
- **Solidity smart contract**
- **Ganache** local development network
- **10 deterministic local accounts with 100 test ETH each**

Hyperledger Fabric is not used by the active runtime. Historical Fabric-era source remains only for migration/reference and must not be treated as an active security boundary.

## System flow

```text
Hospital
  -> de-identification / validation
  -> AES-256-GCM encryption
  -> IPFS encrypted object
  -> hospital-local private CID/SHA metadata
  -> SHA-256 locator commitment on Ethereum
  -> Solidity dataset registry + consent

Researcher
  -> sees discovery metadata
  -> requests access on Ethereum
  -> Hospital approves on Ethereum
  -> backend checks Solidity CanAccess-equivalent policy
  -> approved request enables privacy-preserving HE workflows
  -> plaintext dataset release remains disabled unless explicitly opted in

Homomorphic encryption
  -> Hospital creates Microsoft SEAL CKKS ciphertext
  -> ciphertext bundle stored in IPFS
  -> HE provenance recorded by Solidity
  -> Researcher computes Average or SUM without secret key
  -> encrypted result stored in IPFS
  -> Hospital decrypts final aggregate
  -> Solidity records state transitions
```

No raw medical file, plaintext patient value, AES key, or Microsoft SEAL secret key is written to Ethereum.

Dataset and access-request identifiers written to Ethereum are server-generated opaque values (`ds-<32 hex>` and `req-<32 hex>`); Solidity rejects semantic/non-opaque IDs. Dataset descriptions and access purposes are always represented on Ethereum as `sha256:<digest>` commitments, and the Solidity contract rejects plaintext values. DICOM free-text Study/Series/Protocol descriptions are removed before encrypted storage.

## One-shot local setup

Requirements:

- Ubuntu / WSL2
- Docker
- Node.js 20+
- Python 3.11+
- CMake + C++ compiler
- Microsoft SEAL 4.4 for the HE demo

Install Python dependencies:

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r requirements.txt
```

Start Ganache, compile Solidity, deploy the contract, and run the Solidity E2E test:

```bash
chmod +x scripts/setup_ethereum_local.sh
./scripts/setup_ethereum_local.sh
```

This creates:

```text
ethereum/deployment.json
```

The file contains only local development network metadata and account addresses. Ganache owns the unlocked local test accounts.

Start IPFS if it does not already exist:

```bash
docker volume create medical-ipfs-data
docker run -d --name medical-ipfs \
  -v medical-ipfs-data:/data/ipfs \
  -p 127.0.0.1:5001:5001 \
  ipfs/kubo:v0.43.1
```

Provide a 32-byte dataset-key wrapping secret from outside the repository/key directory, then create API tokens and localhost TLS and start:

```bash
export MEDICAL_MASTER_KEY_HEX="$(openssl rand -hex 32)"
./scripts/start_secure_api.sh
```

The application never writes this master key to its dataset-key directory. Dataset AES keys are stored there only as AES-256-GCM-wrapped `MEDKEY01` blobs. For a real deployment, inject the wrapping secret from a secrets manager/KMS/HSM rather than generating it ad hoc in a shell session.

Open:

```text
https://localhost:8443/
```

## Smart contract

```text
ethereum/contracts/MedicalResearchRegistry.sol
```

The contract implements:

- dataset registration
- private-locator commitment finalization
- consent ACTIVE / RESTRICTED / REVOKED
- researcher access requests
- Hospital approval / rejection / revocation
- current access policy checks
- key-rotation audit records
- dataset/access histories
- HE job registration
- encrypted-computation provenance
- final-decryption provenance

The Hospital is Ganache account 0. The Researcher is Ganache account 1.

## Private locator design

Ethereum is transparent, so the IPFS CID and encrypted-object SHA-256 are not written directly to the public local ledger.

The backend keeps them in the Hospital's local private metadata store and writes only:

```text
keccak256(CID + ":" + SHA256)
```

to Solidity during dataset finalization.

When the Hospital later reads the private record, the backend recomputes and verifies the commitment.

## Automated tests

Contract-only E2E:

```bash
npm --prefix ethereum run test:contract
```

Python Ethereum adapter E2E:

```bash
backend/.venv/bin/python scripts/test_ethereum_adapter.py
```

Full backend E2E requires Ganache + IPFS + built Microsoft SEAL binaries:

```bash
backend/.venv/bin/python scripts/test_ethereum_backend_e2e.py
```

It covers:

1. Hospital dataset upload
2. AES encryption
3. IPFS storage
4. Ethereum registration and locator commitment
5. Researcher access request
6. Hospital approval
7. plaintext download is denied by default; controlled opt-in release is integrity-verified
8. encryption-key rotation audit
9. HE Average
10. HE Average decryption
11. HE ledger/history
12. a fresh HE job for SUM
13. HE SUM decryption
14. request revocation
15. dataset consent revocation

The GitHub Actions workflow `.github/workflows/ethereum-e2e.yml` executes the same migration path on every push to the migration branch.

## Safety / scope

This is a research prototype, not production clinical infrastructure. Use only synthetic or properly de-identified data. It does not claim HIPAA, GDPR, DPDP, or hospital-production compliance.

Researcher plaintext download is disabled by default. For an explicitly controlled local demo only, set `MEDICAL_ALLOW_PLAINTEXT_DOWNLOADS=true`; the default research path is HE/compute-to-data.


## Authorization invariants

- Every HE job is tied to an approved primary dataset request.
- DICOM SEG analysis additionally requires a separate approved request for the SEG dataset.
- The primary and secondary requests must belong to the same researcher wallet.
- Consent/access is re-checked before researcher computation **and again before Hospital decryption**.
- Revoking either required grant blocks further computation/decryption.

## Dataset-key protection

Dataset AES keys are not stored as raw 32-byte files. Each key generation is wrapped with AES-256-GCM using the externally supplied `MEDICAL_MASTER_KEY_HEX`. Legacy raw key files are migrated atomically to the wrapped `MEDKEY01` format on first successful load. Key files and metadata remain Hospital-local with restrictive permissions.

## Key rotation semantics

Dataset key rotation creates a new active AES key generation for future encrypted objects while retaining historical generations as DECRYPT_ONLY so existing immutable IPFS ciphertext remains readable. It is **version rotation**, not retroactive re-encryption. If an old key is suspected compromised, the affected ciphertext must be re-encrypted and republished under a new dataset/version; rotating metadata alone does not remediate historical ciphertext.
