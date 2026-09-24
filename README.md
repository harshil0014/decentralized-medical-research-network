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
  -> RAM-backed plaintext staging only (tmpfs/ramfs)
  -> de-identification / validation
  -> AES-256-GCM encryption
  -> IPFS encrypted object
  -> hospital-local private CID/SHA metadata
  -> SHA-256 locator commitment on Ethereum
  -> Solidity dataset registry + consent
  -> encrypted recovery snapshot refreshed at configured external destination

Researcher
  -> authenticates with an individual service token mapped to a distinct Ethereum wallet
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

Create one Hospital token and one token file per researcher, then map each researcher to a distinct Ganache wallet index:

```bash
AUTH=/root/.medical-registry
mkdir -p "$AUTH"
chmod 700 "$AUTH"

openssl rand -hex 32 > "$AUTH/hospital_api.token"
openssl rand -hex 32 > "$AUTH/researcher_a.token"
openssl rand -hex 32 > "$AUTH/researcher_b.token"
chmod 600 "$AUTH"/*.token

cat > "$AUTH/researchers.json" <<'JSON'
{
  "schemaVersion": 1,
  "researchers": [
    {"id": "researcher-a", "tokenFile": "researcher_a.token", "walletIndex": 1},
    {"id": "researcher-b", "tokenFile": "researcher_b.token", "walletIndex": 2}
  ]
}
JSON
chmod 600 "$AUTH/researchers.json"
```

Ganache wallet index 0 is reserved for the Hospital. Researcher wallet indexes 1 through 9 are available, and duplicate researcher IDs, tokens, or wallet indexes are rejected at API startup.

The dataset-key wrapping secret must be **persistent across restarts** and supplied from outside the repository/key directory. Do not generate a new value every time the API starts. Also configure a recovery bundle destination outside the dataset-key directory, preferably on a separately backed-up or mounted volume:

```bash
export MEDICAL_MASTER_KEY_HEX="<persistent 64-hex secret from your secret manager>"
export MEDICAL_RECOVERY_BACKUP_PATH="/mnt/medical-recovery/medical-recovery.medrec"
./scripts/start_secure_api.sh
```

The secure launcher and the FastAPI process force multipart/plaintext staging onto verified Linux `tmpfs`/`ramfs` storage (default `/dev/shm/medical-registry-plaintext`). The application never writes the master key to its dataset-key directory. Dataset AES keys are stored there only as AES-256-GCM-wrapped `MEDKEY01` blobs.

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

The Hospital is Ganache account 0. Individual researcher identities are assigned distinct Ganache accounts 1 through 9 through `/root/.medical-registry/researchers.json`.

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
16. two independent researcher tokens map to different Ethereum wallets
17. cross-researcher download/HE use is rejected
18. medical upload staging is RAM-backed
19. encrypted recovery-bundle authentication, destructive key/locator loss, and verified restore

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

## Plaintext staging

Medical uploads are fail-closed unless a RAM-backed staging filesystem is available. The process verifies `tmpfs`/`ramfs`, points Python/Starlette multipart temp storage there, and also creates the application staging file there. Normal-disk deletion is therefore not relied on as a PHI-erasure mechanism.

## Researcher identity isolation

Each configured researcher has a unique service token, researcher ID, and Ganache wallet index. Access requests are signed by that identity's wallet. HE computation and controlled plaintext-release checks verify that the authenticated researcher's wallet matches the request/job researcher. The Solidity contract independently enforces the researcher address recorded on the HE job.

## Dataset-key protection

Dataset AES keys are not stored as raw 32-byte files. Each key generation is wrapped with AES-256-GCM using the externally supplied `MEDICAL_MASTER_KEY_HEX`. Legacy raw key files are migrated atomically to the wrapped `MEDKEY01` format on first successful load. Key files and metadata remain Hospital-local with restrictive permissions.

## Key rotation semantics

Dataset key rotation creates a new active AES key generation for future encrypted objects while retaining historical generations as DECRYPT_ONLY so existing immutable IPFS ciphertext remains readable. It is **version rotation**, not retroactive re-encryption. If an old key is suspected compromised, the affected ciphertext must be re-encrypted and republished under a new dataset/version; rotating metadata alone does not remediate historical ciphertext.


## Disaster recovery

Successful dataset registration and key rotation automatically refresh an encrypted `MEDREC01` recovery bundle at `MEDICAL_RECOVERY_BACKUP_PATH`. The bundle contains the wrapped dataset-key registry and Hospital-private CID/SHA locators, is AES-GCM authenticated under a recovery key derived from the external master secret, and is bound to the current Ethereum chain ID and contract address.

Hospital-only recovery endpoints support snapshot, encrypted export, and transactional restore. Restore validates bundle authentication, file hashes, chain/contract identity, dataset-key metadata and every restored private locator commitment against Ethereum. A failed verification rolls the local restore back. Keep the configured recovery path in a separate backup failure domain; software cannot protect against losing the external master secret and every copy of the recovery bundle simultaneously.
