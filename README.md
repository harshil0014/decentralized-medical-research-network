# Decentralized Medical Research Network

Local research prototype for privacy-preserving medical-data sharing and encrypted analytics.

## Current blockchain stack

The active governance layer is:

- **Ethereum-compatible local blockchain**
- **Solidity smart contract**
- **Four-validator Besu QBFT** permissioned demo network
- **Three connected IPFS/Kubo peers** with three-way encrypted-object pinning

Ganache is retained only for the faster compatibility/E2E test environment.

Hyperledger Fabric is not used by the active runtime. Historical Fabric source and scripts are quarantined under `archive/fabric/` for reference only.

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
  -> authenticates with an individual service token bound to an external Ethereum wallet address
  -> signs requests and HE computations in MetaMask/EIP-1193
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

Dataset and access-request identifiers written to Ethereum are server-generated opaque values (`ds-<32 hex>` and `req-<32 hex>`); Solidity rejects semantic/non-opaque IDs. Dataset descriptions and access purposes are always represented on Ethereum as `sha256:<digest>` commitments. CSV metric names and DICOM analysis details appear only as `CSV:sha256:<digest>` or `DICOM:sha256:<digest>`. Solidity validates these formats. DICOM free-text Study/Series/Protocol descriptions are removed before encrypted storage.

## Decentralized demo setup

For the complete operator sequence from network startup through wallet signatures, DICOM SEG, outages, recovery, remediation, and restart, see [the demo walkthrough](docs/DEMO_WALKTHROUGH.md).

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
pip install -r requirements.lock
```

Start the four-validator Besu QBFT network and three IPFS peers, compile and deploy the contract, and run its E2E test:

```bash
bash scripts/setup_decentralized_demo.sh
source .runtime/decentralized/demo.env
bash scripts/status_decentralized_demo.sh
PYTHONPATH=. python3 scripts/test_decentralized_topology.py
```

The test checks four distinct validators and three distinct IPFS peers, verifies contract state from all four RPC endpoints, retrieves a replicated object with the primary IPFS peer down, and checks ledger availability with one validator down. To stop the demo while retaining node data:

```bash
bash scripts/stop_decentralized_demo.sh
```

The setup creates:

```text
ethereum/deployment.json
```

The deployment file contains network metadata and public addresses. Besu RPC nodes do not hold researcher keys. The Hospital transaction key is stored in the external `~/.medical-decentralized/authority` directory by default. The backend tries the RPC endpoints in `MEDICAL_ETHEREUM_RPC_URLS` in order and fails over on transport failure.

On restart, setup checks that the selected Hospital key is funded by the persisted QBFT genesis and that the recorded contract still exists on the chain. It refuses a different Hospital key, and redeploys only when the recorded contract is absent. Preserve the original authority key with the node volumes; moving an existing dataset to another authority or contract requires the explicit encrypted migration workflow.

For the faster single-node Ganache CI compatibility path only:

```bash
bash scripts/setup_ethereum_local.sh
```

Create one Hospital token and one token file per researcher. Bind each researcher to the public address of a wallet they control in MetaMask. Private keys and seed phrases never belong in this registry or in backend environment variables:

```bash
AUTH="${MEDICAL_REGISTRY_AUTH_DIR:-$HOME/.medical-decentralized/authority}"
mkdir -p "$AUTH"
chmod 700 "$AUTH"

openssl rand -hex 32 > "$AUTH/hospital_api.token"
openssl rand -hex 32 > "$AUTH/researcher_a.token"
openssl rand -hex 32 > "$AUTH/researcher_b.token"
chmod 600 "$AUTH"/*.token

cat > "$AUTH/researchers.json" <<'JSON'
{
  "schemaVersion": 2,
  "researchers": [
    {"id": "researcher-a", "tokenFile": "researcher_a.token", "walletAddress": "0x<researcher-a-address>"},
    {"id": "researcher-b", "tokenFile": "researcher_b.token", "walletAddress": "0x<researcher-b-address>"}
  ]
}
JSON
chmod 600 "$AUTH/researchers.json"
```

The address must match the wallet connected in the browser. Duplicate researcher IDs, tokens, or wallet addresses are rejected at API startup. The backend relays signed actions and pays demo gas through the Hospital account; it cannot sign as a researcher. `demo.env` points `MEDICAL_REGISTRY_AUTH_DIR` at the external Hospital authority directory; place token files and `researchers.json` there.

The dataset-key wrapping secret must be **persistent across restarts** and supplied from outside the repository/key directory. Do not generate a new value every time the API starts. Also configure a recovery bundle destination outside the dataset-key directory, preferably on a separately backed-up or mounted volume:

```bash
export MEDICAL_MASTER_KEY_HEX="<persistent 64-hex secret from your secret manager>"
export MEDICAL_RECOVERY_BACKUP_PATH="/mnt/medical-recovery/medical-recovery.medrec"
export MEDICAL_DATA_BACKUP_DIR="/mnt/medical-recovery/encrypted-objects"
./scripts/start_secure_api.sh
```

The secure launcher and the FastAPI process force multipart/plaintext staging **and the entire SEAL HE job workspace** (including transient plaintext inputs and Hospital HE secret keys) onto verified Linux `tmpfs`/`ramfs` storage (default `/dev/shm/medical-registry-plaintext`). The application never writes the master key to its dataset-key directory. Dataset AES keys are stored there only as AES-256-GCM-wrapped `MEDKEY01` blobs.

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

The Hospital controls governance and final HE decryption. Researchers control their own wallet keys. EIP-191 signatures bind chain ID, contract address, action and request/job identifiers; Solidity verifies the claimed researcher address and rejects high-s signatures.

## Private locator design

Ethereum is transparent, so the IPFS CID and encrypted-object SHA-256 are not written directly to the public local ledger.

The backend keeps them in the Hospital's local private metadata store and writes only:

```text
keccak256(CID + ":" + SHA256)
```

to Solidity during dataset finalization.

HE ciphertext and result CIDs are likewise stored in a Hospital-private locator file (`MEDICAL_ETHEREUM_HE_LOCATORS`); the contract stores only `sha256:<digest>` commitments. Authorized backend reads verify each private CID against its on-chain commitment. Public job/history responses expose commitments without CIDs. The encrypted recovery bundle includes this private HE locator file, and deliberate contract migration clears it because old HE jobs and researcher signatures remain bound to the source deployment.

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

Full backend E2E requires a local Ethereum RPC, IPFS and built Microsoft SEAL binaries:

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
19. encrypted recovery-bundle authentication, destructive key/locator/IPFS loss, and verified restore
20. plaintext IPFS object rejection and wallet signature checks

The GitHub Actions workflow `.github/workflows/ethereum-e2e.yml` runs the full backend suite and the separate four-validator/three-peer topology suite on pushes to `dicom-he-v1`.

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

Each configured researcher has a unique service token, researcher ID, and externally owned wallet address. Access requests and HE computations are signed by that wallet. The backend checks the authenticated address before relay, and Solidity independently checks the claimed request signer and the approved HE-job researcher.

## Dataset-key protection

Dataset AES keys are not stored as raw 32-byte files. Each key generation is wrapped with AES-256-GCM using the externally supplied `MEDICAL_MASTER_KEY_HEX`. Legacy raw key files are migrated atomically to the wrapped `MEDKEY01` format on first successful load. Key files and metadata remain Hospital-local with restrictive permissions.

## Key rotation semantics

Dataset key rotation creates a new active AES key generation for future encrypted objects while retaining historical generations as DECRYPT_ONLY so existing immutable IPFS ciphertext remains readable. It is **version rotation**, not retroactive re-encryption. For a suspected compromised key, the Hospital uses `POST /datasets/{dataset_id}/remediate-key`: it decrypts only in RAM, re-encrypts under a fresh generation, verifies the replicated MEDAES object, stages its encrypted backup, atomically updates the on-chain commitment and key audit, refreshes recovery, then unpins the old object. A journal lets the Hospital retry an interrupted finalization. Copies of old ciphertext or a key already obtained by an adversary cannot be clawed back.

## CSV HE cohort limit

The CSV HE demo accepts 2–1000 numeric values for one selected metric. The 1000-row synthetic boundary is covered by CI; larger cohorts require a different batching design and are rejected explicitly. CKKS results are approximate, and their units come from the selected source CSV column; the API does not assume a glucose unit for arbitrary metrics.

## DICOM analysis semantics

Classic CT/MR slices are ordered by their patient-space plane normal. Inconsistent orientation or overlapping planes are rejected. Physical voxel volume is reported only when slice positions prove regular spacing; `TOTAL_ENERGY` requires that spacing. SEG analysis requires matching source references, frame of reference, orientation, matrix geometry and complete plane coverage. Independently uploaded source and SEG objects use the Hospital master key to create consistent pseudonymous UIDs.

The CT/MR HE loader accepts classic single-frame slices. Multi-frame CT/MR objects are rejected because this prototype does not reconstruct or verify their per-frame geometry.

`RAW_VOXELS` encrypts selected voxel values before researcher computation. It streams CKKS ciphertext chunks and checks free RAM-backed workspace capacity before encryption. `BLOCK_STATS` has the Hospital compute plaintext sums and squared sums, then encrypts those sufficient statistics; higher moments require `RAW_VOXELS`. Both modes return approximate CKKS results without a fixed error guarantee. Pixel identifiers are addressed by explicit Hospital visual review, separate from automated DICOM header de-identification. No automated pixel-PHI detector is claimed.

Dataset browsing and history endpoints support `offset` and `limit` query parameters (maximum 100 records). `/health` returns only service liveness; detailed network diagnostics require Hospital authentication at `/admin/diagnostics`. Researcher service credentials can be disabled in `researchers.json` with `"enabled": false` or revoked by removing the token file. Browser credentials use session storage for this localhost demo and logout clears that storage.

`requirements.lock` records the tested Python dependency versions used in CI and fresh setup. Intentional dependency updates should start from `requirements.txt`, regenerate the lock with `python -m pip freeze`, and rerun both CI jobs. Microsoft SEAL and demo container images are pinned to tested revisions; compiler warnings inside bundled SEAL/zstd are upstream warnings.


## Disaster recovery

Successful dataset registration and key rotation automatically refresh an encrypted `MEDREC01` recovery bundle at `MEDICAL_RECOVERY_BACKUP_PATH`. The bundle contains the wrapped dataset-key registry, Hospital-private CID/SHA locators and an authenticated manifest of encrypted-object backups. The encrypted MEDAES objects themselves live in `MEDICAL_DATA_BACKUP_DIR`, outside the primary key/auth/IPFS directories. The bundle is AES-GCM authenticated under a recovery key derived from the external master secret and bound to the current Ethereum chain ID and contract address.

Hospital-only recovery endpoints support snapshot, encrypted export, and restore. Restore validates bundle authentication, encrypted-object SHA, chain/contract identity, dataset-key metadata and every private locator commitment against Ethereum, then restores missing encrypted objects to the replicated IPFS layer and verifies their CIDs. A failed verification rolls local keys and locators back. Both backup destinations must be in a separate failure domain; the demo exports `MEDICAL_IPFS_DATA_ROOT` so backup paths inside the primary IPFS data directory are rejected. Historical pre-encryption prototype data must be re-imported and re-encrypted; plaintext IPFS compatibility was intentionally removed.

Ordinary restore remains bound to its source chain and contract. For a deliberate redeployment, the Hospital can call `POST /admin/recovery/migrate` with the source MEDREC bundle plus `source_rpc_url`, `source_chain_id`, and `source_contract_address`. Copy the external encrypted-object backup directory to the destination first and start with an empty destination key/locator store. Migration verifies source ledger commitments and Hospital ownership, registers matching destination records, records a migration provenance event, restores wrapped keys and encrypted objects, then creates a destination-bound recovery snapshot. Access requests and researcher signatures are deployment-bound and must be created again on the destination.
