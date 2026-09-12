# On-chain data model

## DatasetRecord

Only governance metadata is on-chain.

- datasetId
- CID (pointer, eventually from Private IPFS)
- SHA-256 (integrity)
- ownerOrg
- dataType
- metadataSummary — must contain no PHI
- consentState
- version
- timestamps

## AccessRequest

- requestId
- datasetId
- requesterOrg
- purpose
- status
- requestedAt
- decidedAt
- decidedBy

## Never put on-chain

- DICOM pixels
- patient name
- patient ID
- date of birth
- complete clinical notes
- raw lab reports
- encryption keys
