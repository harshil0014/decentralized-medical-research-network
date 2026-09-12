# Hyperledger Fabric Medical Registry — Prototype 1

This is a deliberately small **proof-of-concept** for the blockchain part of the medical-data project.
It does **not** store MRI/DICOM files on-chain.

## What this prototype proves

A Fabric network can keep a tamper-evident governance record for a medical dataset:

- register dataset ID + CID + SHA-256 + owner organisation
- keep consent state (`ACTIVE`, `RESTRICTED`, `REVOKED`)
- create researcher access requests
- approve / reject / revoke access
- emit events
- query current state
- ask `CanAccess` before off-chain release
- inspect dataset/access history

The values are dummy/synthetic. No patient data belongs in this prototype.

## Why do this first?

Your full system has many moving parts: DICOM, IPFS, FHIR, federated search, AI and Fabric.
This prototype isolates one question:

> Can Hyperledger Fabric reliably act as our permission + ownership + audit layer?

If yes, we later connect the universal core and Private IPFS to it.

## Network we use

Use the official `fabric-samples/test-network` first. It provides two peer organisations,
one peer per organisation, and a Raft ordering service. The official scripts create a channel
and deploy chaincode for development/testing.

## Recommended Windows setup

Fabric's current docs recommend **Docker Desktop + WSL2** on Windows because the samples
and docs use Bash heavily.

Inside Ubuntu/WSL:

```bash
sudo apt update
sudo apt install -y git curl jq

docker --version
docker compose version
```

Docker Desktop -> Settings -> Resources -> WSL Integration -> enable your Ubuntu distro.

## Step 1 — Install official Fabric samples

Follow the current Hyperledger Fabric install documentation to obtain `fabric-samples`,
Fabric binaries/config, and Docker images. Do not copy random third-party installation scripts.

After installation, you should have:

```text
fabric-samples/
  bin/
  config/
  test-network/
```

## Step 2 — Copy this prototype chaincode into fabric-samples

Example:

```bash
cp -r /path/to/fabric-medical-registry-prototype \
  ~/fabric-samples/medical-registry-prototype
```

## Step 3 — Start the official test network

```bash
cd ~/fabric-samples/test-network
./network.sh down
./network.sh up createChannel -c mychannel -ca
```

## Step 4 — Deploy our medical-registry chaincode

From `test-network`:

```bash
./network.sh deployCC \
  -ccn medicalregistry \
  -ccp ../medical-registry-prototype \
  -ccl go
```

## Step 5 — Set peer CLI environment

```bash
export PATH=${PWD}/../bin:$PATH
export FABRIC_CFG_PATH=${PWD}/../config/
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_LOCALMSPID="Org1MSP"
export CORE_PEER_TLS_ROOTCERT_FILE=${PWD}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt
export CORE_PEER_MSPCONFIGPATH=${PWD}/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp
export CORE_PEER_ADDRESS=localhost:7051
```


## Important: organisations are taken from Fabric identity

This prototype does **not** trust a caller-supplied string such as `HospitalA`.

- the organisation that registers a dataset is read from the caller's Fabric MSP ID
- the organisation that requests access is read from the caller's Fabric MSP ID
- only the dataset owner's MSP can update consent or approve/reject/revoke access

In the official test network we use **Org1MSP as the hospital** and **Org2MSP as the researcher**.
This makes the prototype demonstrate real Fabric identity-based authorization rather than only storing status text.

## Step 6 — Register one dummy MRI dataset as Org1 (hospital)

From `fabric-samples/test-network`, first load Org1 identity:

```bash
source ../medical-registry-prototype/scripts/env-org1.sh
```


```bash
peer chaincode invoke \
  -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
  --tls --cafile "${PWD}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem" \
  -C mychannel -n medicalregistry \
  --peerAddresses localhost:7051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt" \
  --peerAddresses localhost:9051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt" \
  -c '{"function":"RegisterDataset","Args":["MRI-DEMO-001","bafy-demo-not-a-real-ipfs-cid","0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","MRI_DICOM","Synthetic brain MRI; no PHI; prototype only","ACTIVE"]}'
```

## Step 7 — Read it back

```bash
peer chaincode query -C mychannel -n medicalregistry \
  -c '{"Args":["ReadDataset","MRI-DEMO-001"]}'
```

Expected shape:

```json
{
  "datasetId": "MRI-DEMO-001",
  "cid": "bafy-demo-not-a-real-ipfs-cid",
  "sha256": "...",
  "ownerOrg": "Org1MSP",
  "dataType": "MRI_DICOM",
  "metadataSummary": "Synthetic brain MRI; no PHI; prototype only",
  "consentState": "ACTIVE",
  "version": 1
}
```

## Step 8 — Researcher requests access as Org2

Switch the CLI identity to Org2:

```bash
source ../medical-registry-prototype/scripts/env-org2.sh
```


```bash
peer chaincode invoke \
  -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
  --tls --cafile "${PWD}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem" \
  -C mychannel -n medicalregistry \
  --peerAddresses localhost:7051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt" \
  --peerAddresses localhost:9051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt" \
  -c '{"function":"RequestAccess","Args":["REQ-001","MRI-DEMO-001","Brain tumour AI research"]}'
```

Query:

```bash
peer chaincode query -C mychannel -n medicalregistry \
  -c '{"Args":["ReadAccessRequest","REQ-001"]}'
```

## Step 9 — Hospital approves the request

Switch back to Org1. The chaincode checks that this MSP owns the dataset:

```bash
source ../medical-registry-prototype/scripts/env-org1.sh
```


```bash
peer chaincode invoke \
  -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
  --tls --cafile "${PWD}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem" \
  -C mychannel -n medicalregistry \
  --peerAddresses localhost:7051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt" \
  --peerAddresses localhost:9051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt" \
  -c '{"function":"DecideAccess","Args":["REQ-001","APPROVED"]}'
```

## Step 10 — Check whether off-chain access is currently allowed

```bash
peer chaincode query -C mychannel -n medicalregistry \
  -c '{"Args":["CanAccess","REQ-001"]}'
```

After approval and while consent is `ACTIVE`, the result should be `true`. This is the
function our later API/IPFS layer can call before releasing the object.

## Step 11 — Revoke dataset consent

Remain as Org1; a different MSP will be rejected by the chaincode.


```bash
peer chaincode invoke \
  -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
  --tls --cafile "${PWD}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem" \
  -C mychannel -n medicalregistry \
  --peerAddresses localhost:7051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt" \
  --peerAddresses localhost:9051 \
  --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt" \
  -c '{"function":"UpdateConsent","Args":["MRI-DEMO-001","REVOKED"]}'
```

Now query `CanAccess` again. It should return `false` even though the access request was
previously approved, because dataset consent is no longer active.

```bash
peer chaincode query -C mychannel -n medicalregistry \
  -c '{"Args":["CanAccess","REQ-001"]}'
```

## Prototype PASS condition

We call Prototype 1 successful when all of these work:

1. Fabric test network starts.
2. `medicalregistry` chaincode deploys.
3. A dummy MRI registry record is written.
4. The same record is queryable.
5. A research access request can be created.
6. The request can be approved/rejected/revoked.
7. Consent state can be changed.
8. `CanAccess` returns true only for approved access while dataset consent is active.
9. History/audit information can be queried.
10. No raw MRI/patient information is written to the ledger.

## What comes immediately after this

Do **not** add AI yet.

Next prototype:

```text
synthetic file
  -> SHA-256
  -> local/private IPFS
  -> CID
  -> RegisterDataset on Fabric
  -> request access
  -> retrieve only after approval
```

That connects Fabric to the universal backbone without changing the project architecture.
