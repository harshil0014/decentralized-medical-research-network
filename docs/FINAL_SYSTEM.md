# Final Medical Research Prototype

Implemented and verified:
- AES-256-GCM encrypted medical data in IPFS
- Hyperledger Fabric v1.11 sequence 12
- CID/SHA stored in Org1 Fabric private data for new datasets
- Hospital/researcher role authentication
- HTTPS on localhost:8443
- Security headers and disabled API docs
- Serialized state-changing operations
- Consent-based research access
- Microsoft SEAL CKKS encrypted computation
- Researcher computes without the HE secret key
- Full secure E2E regression passed

Important limitations:
- Research prototype, not production clinical infrastructure
- No HIPAA/GDPR compliance claim
- Service tokens instead of individual OIDC identities
- Local key storage instead of KMS/HSM
- DICOM de-identification is not complete PS3.15
- HE currently targets numeric CSV statistics
- Legacy blockchain CID/SHA history cannot be erased
- Approved download still exposes plaintext to an authorized researcher

Research direction:
Privacy-preserving decentralized biomedical research using encrypted off-chain storage, decentralized governance, private metadata, auditable access and model-to-data computation.
