# Reproducible decentralized demo walkthrough

Use synthetic data only. Run commands from the repository root on Ubuntu or WSL2. Complete the setup and persistent secret configuration in [README](../README.md#decentralized-demo-setup) first. Keep `MEDICAL_MASTER_KEY_HEX`, the Hospital token, and researcher wallet keys outside the repository. The researcher connects a wallet they control; the backend only receives its public address and signatures.

## Start and inspect the network

1. Run `bash scripts/setup_decentralized_demo.sh` and `source .runtime/decentralized/demo.env`.
2. Run `bash scripts/status_decentralized_demo.sh`. It must report four healthy Besu QBFT validators and three healthy IPFS peers.
3. Run `PYTHONPATH=. python3 scripts/test_decentralized_topology.py` to prove distinct nodes, shared contract state, replicated IPFS retrieval with a peer down, validator quorum with one node down, and persistence after a full stop/start. This test briefly stops demo containers, so run it before interactive work.
4. Set the persistent master secret and external recovery destinations as shown in the README, then start `./scripts/start_secure_api.sh`. Open `https://localhost:8443/`.

## Hospital and researcher workflow

5. Log in with the Hospital token. In a separate browser profile, log in with a researcher token from `researchers.json`.
6. Connect the researcher browser to the same Besu chain with its EIP-1193 wallet. Its selected address must match that researcher's registry address. The browser signs requests and HE compute actions; neither its private key nor seed phrase is entered into the API.
7. In the Hospital **Datasets** view, upload a synthetic CSV. Record the generated opaque dataset ID. The upload encrypts the CSV as MEDAES before replicated IPFS storage and stores only commitments on Ethereum.
8. In the researcher **Requests** view, create an access request with a purpose. The wallet signs the request. In the Hospital view, approve it. Record the opaque request ID.
9. In Hospital **HE**, encrypt the CSV metric and record the job ID. In researcher **HE**, compute **Average** with a wallet signature. In Hospital **HE**, decrypt the result. Repeat with a fresh job and **Compute Sum**. Results identify CKKS approximation.
10. Upload a synthetic CT or MR series and a matching synthetic DICOM SEG as separate Hospital datasets. The CT/MR upload requires explicit Hospital visual review attestation for pixel PHI; header de-identification alone does not perform that review.
11. The researcher creates separate wallet-signed requests for the CT/MR dataset and SEG dataset. The Hospital approves both. In Hospital **DICOM HE Encrypt**, select the source request, SEG scope, SEG request, and segment number. The researcher signs **DICOM HE Compute**; the Hospital decrypts the result. The response records whether `RAW_VOXELS` or `BLOCK_STATS` was used.
12. Revoke a request in Hospital **Requests**, then try another compute/decrypt or new DICOM analysis under that grant. It must be denied. Consent revocation can be demonstrated in Hospital **Datasets** as well.

## Outages, recovery, and remediation

13. After interactive work, rerun `PYTHONPATH=. python3 scripts/test_decentralized_topology.py`. It stops one IPFS peer and one validator in turn, confirms reads and transactions continue, then restarts all seven nodes and verifies persisted state and pins.
14. With the Hospital token, call `POST /admin/recovery/snapshot` and save `GET /admin/recovery/export` to an external location. Preserve `MEDICAL_DATA_BACKUP_DIR` alongside the encrypted MEDREC bundle. The bundle alone contains keys and private locators, while the external encrypted-object directory contains the MEDAES objects; neither contains plaintext medical data.
15. For a **destructive recovery demonstration**, use an isolated synthetic test environment and run `PYTHONPATH=. python3 scripts/test_ethereum_backend_e2e.py`. It deletes wrapped keys, private locators, and every original IPFS copy of a synthetic encrypted object, proves access fails, restores from the external backups, and verifies key, SHA, CID, Ethereum commitment, and authorized access. It also checks corruption, wrong key, and wrong deployment rejection. Do not delete a live demo's only backup.
16. To demonstrate compromised-key remediation on a synthetic active dataset, call `POST /datasets/{dataset_id}/remediate-key` as Hospital. The response gives the new key version and commitment. If a 503 says finalization is incomplete, preserve both encrypted copies and repeat the same endpoint until it reconciles. The backend E2E exercises an interrupted snapshot and retry.
17. Run `bash scripts/stop_decentralized_demo.sh`, then `bash scripts/setup_decentralized_demo.sh` and `bash scripts/status_decentralized_demo.sh`. Existing Docker volumes retain Besu state and IPFS pins; verify the earlier dataset and job through the API after restarting the backend with the same master secret and auth directory.

The contract migration case is covered by `scripts/test_ethereum_backend_e2e.py`: ordinary cross-contract restore fails, while the explicit Hospital migration verifies source commitments and rebinds encrypted backups and dataset provenance to the destination. Access requests and signatures must be made again after migration.
