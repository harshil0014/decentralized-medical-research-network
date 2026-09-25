# On-chain data model

## Dataset

- datasetId — restricted safe identifier; do not encode patient information
- owner wallet
- dataType
- metadataSummary — mandatory `sha256:<digest>` commitment; Solidity rejects plaintext
- consentState
- storageState
- locatorCommitment
- version/timestamps

## AccessRequest

- requestId — restricted safe identifier
- datasetId
- requester wallet
- purpose — `sha256:<digest>` commitment of the submitted free-text purpose
- status/timestamps/decider

## HEJobRecord

- primary datasetId + requestId
- optional secondaryDatasetId + secondaryRequestId for DICOM SEG/multi-dataset work
- metric/cohort size
- encrypted artifact CIDs/hashes
- owner/researcher wallets
- state/timestamps

## Never place on-chain

- DICOM pixels
- patient name/ID/DOB
- clinical notes or raw lab reports
- AES or Microsoft SEAL secret keys
- plaintext free-text research purpose
- arbitrary free-text DICOM descriptors
