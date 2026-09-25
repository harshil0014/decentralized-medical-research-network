// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract MedicalResearchRegistry {
    address public immutable hospital;

    struct Dataset {
        string datasetId;
        address owner;
        string dataType;
        string metadataSummary;
        string consentState;
        string storageState;
        bytes32 locatorCommitment;
        uint256 version;
        uint64 createdAt;
        uint64 updatedAt;
        bool exists;
    }

    struct AccessRequest {
        string requestId;
        string datasetId;
        address requester;
        string purpose;
        string status;
        uint64 requestedAt;
        uint64 decidedAt;
        address decidedBy;
        bool exists;
    }

    struct KeyRotationRecord {
        string datasetId;
        uint256 previousKeyVersion;
        uint256 newKeyVersion;
        address owner;
        string status;
        uint64 rotatedAt;
        bool exists;
    }

    struct HEJobRecord {
        string jobId;
        string datasetId;
        string requestId;
        string secondaryDatasetId;
        string secondaryRequestId;
        string metric;
        uint256 cohortSize;
        string ciphertextLocatorCommitment;
        string ciphertextManifestSha256;
        string resultLocatorCommitment;
        string resultSha256;
        address owner;
        address researcher;
        string status;
        uint64 createdAt;
        uint64 computedAt;
        uint64 decryptedAt;
        bool exists;
    }

    mapping(string => Dataset) private datasets;
    mapping(string => uint256) private datasetIndexPlusOne;
    string[] private datasetIds;
    mapping(string => Dataset[]) private datasetHistory;

    mapping(string => AccessRequest) private requests;
    mapping(string => AccessRequest[]) private accessHistory;

    mapping(bytes32 => KeyRotationRecord) private keyRotations;

    mapping(string => HEJobRecord) private heJobs;
    mapping(string => HEJobRecord[]) private heJobHistory;
    mapping(string => bytes32) public migrationSource;

    event DatasetRegistered(string indexed datasetId, address indexed owner);
    event DatasetFinalized(string indexed datasetId, bytes32 locatorCommitment);
    event DatasetConsentUpdated(string indexed datasetId, string consentState);
    event AccessRequested(string indexed requestId, string indexed datasetId, address indexed requester);
    event AccessDecisionRecorded(string indexed requestId, string status, address indexed decidedBy);
    event DatasetKeyRotated(string indexed datasetId, uint256 previousVersion, uint256 newVersion);
    event DatasetCiphertextReplaced(string indexed datasetId, bytes32 oldCommitment, bytes32 newCommitment, uint256 newKeyVersion);
    event DatasetMigrationRecorded(string indexed datasetId, uint256 sourceChainId, address indexed sourceContract);
    event HEJobRegistered(string indexed jobId, string indexed datasetId, address indexed researcher);
    event HEComputationRecorded(string indexed jobId, string resultLocatorCommitment, string resultSha256);
    event HEDecryptionRecorded(string indexed jobId);

    modifier onlyHospital() {
        require(msg.sender == hospital, "hospital only");
        _;
    }

    constructor() {
        hospital = msg.sender;
    }

    function _eq(string memory a, string memory b) private pure returns (bool) {
        return keccak256(bytes(a)) == keccak256(bytes(b));
    }

    function _validConsent(string memory value) private pure returns (bool) {
        return _eq(value, "ACTIVE") || _eq(value, "RESTRICTED") || _eq(value, "REVOKED");
    }

    function _validPublicDataType(string memory value) private pure returns (bool) {
        return _eq(value, "CSV") || _eq(value, "DICOM") || _eq(value, "DICOM_SEG");
    }

    function _isLowerHex(bytes1 ch) private pure returns (bool) {
        return (ch >= 0x30 && ch <= 0x39) || (ch >= 0x61 && ch <= 0x66);
    }

    function _validOpaqueId(
        string memory value,
        string memory prefixValue
    ) private pure returns (bool) {
        bytes memory raw = bytes(value);
        bytes memory prefix = bytes(prefixValue);
        if (raw.length != prefix.length + 32) return false;
        for (uint256 i = 0; i < prefix.length; i++) {
            if (raw[i] != prefix[i]) return false;
        }
        for (uint256 i = prefix.length; i < raw.length; i++) {
            if (!_isLowerHex(raw[i])) return false;
        }
        return true;
    }

    function _validSha256Commitment(string memory value) private pure returns (bool) {
        bytes memory raw = bytes(value);
        if (raw.length != 71) return false;
        bytes memory prefix = bytes("sha256:");
        for (uint256 i = 0; i < prefix.length; i++) {
            if (raw[i] != prefix[i]) return false;
        }
        for (uint256 i = prefix.length; i < raw.length; i++) {
            if (!_isLowerHex(raw[i])) return false;
        }
        return true;
    }

    function _validHEMetricDescriptor(string memory value) private pure returns (bool) {
        bytes memory raw = bytes(value);
        bytes memory csvPrefix = bytes("CSV:sha256:");
        bytes memory dicomPrefix = bytes("DICOM:sha256:");
        bytes memory prefix;

        if (raw.length == csvPrefix.length + 64) {
            prefix = csvPrefix;
        } else if (raw.length == dicomPrefix.length + 64) {
            prefix = dicomPrefix;
        } else {
            return false;
        }

        for (uint256 i = 0; i < prefix.length; i++) {
            if (raw[i] != prefix[i]) return false;
        }
        for (uint256 i = prefix.length; i < raw.length; i++) {
            if (!_isLowerHex(raw[i])) return false;
        }
        return true;
    }

    function _ethSignedMessageHash(bytes32 digest) private pure returns (bytes32) {
        return keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", digest)
        );
    }

    function _recoverSigner(
        bytes32 digest,
        bytes calldata signature
    ) private pure returns (address) {
        require(signature.length == 65, "invalid researcher signature");

        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }

        if (v < 27) v += 27;
        require(v == 27 || v == 28, "invalid signature v");

        // Reject high-s malleable signatures (secp256k1n / 2).
        require(
            uint256(s)
                <= 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0,
            "invalid signature s"
        );

        address signer = ecrecover(
            _ethSignedMessageHash(digest),
            v,
            r,
            s
        );
        require(signer != address(0), "invalid researcher signature");
        return signer;
    }

    function researcherRequestDigest(
        string calldata requestId,
        string calldata datasetId,
        string calldata purpose
    ) public view returns (bytes32) {
        return keccak256(
            abi.encode(
                "MEDICAL_REQUEST_V1",
                block.chainid,
                address(this),
                requestId,
                datasetId,
                purpose
            )
        );
    }

    function heComputeDigest(
        string calldata jobId
    ) public view returns (bytes32) {
        HEJobRecord storage job = heJobs[jobId];
        require(job.exists, "HE job missing");
        return keccak256(
            abi.encode(
                "MEDICAL_HE_COMPUTE_V1",
                block.chainid,
                address(this),
                jobId,
                job.datasetId,
                job.requestId,
                job.secondaryDatasetId,
                job.secondaryRequestId,
                job.metric,
                job.cohortSize,
                job.ciphertextLocatorCommitment,
                job.ciphertextManifestSha256,
                job.researcher
            )
        );
    }

    function _pushDatasetHistory(string memory datasetId) private {
        Dataset memory snapshot = datasets[datasetId];
        datasetHistory[datasetId].push(snapshot);
    }

    function _pushAccessHistory(string memory requestId) private {
        AccessRequest memory snapshot = requests[requestId];
        accessHistory[requestId].push(snapshot);
    }

    function _pushHEHistory(string memory jobId) private {
        HEJobRecord memory snapshot = heJobs[jobId];
        heJobHistory[jobId].push(snapshot);
    }

    function registerDataset(
        string calldata datasetId,
        string calldata dataType,
        string calldata metadataSummary,
        string calldata consentState
    ) external onlyHospital {
        require(_validOpaqueId(datasetId, "ds-"), "opaque datasetId required");
        require(_validPublicDataType(dataType), "invalid public dataType");
        require(_validSha256Commitment(metadataSummary), "metadata commitment required");
        require(_validConsent(consentState), "invalid consent");
        require(!datasets[datasetId].exists, "dataset exists");

        uint64 nowTs = uint64(block.timestamp);
        datasets[datasetId] = Dataset({
            datasetId: datasetId,
            owner: msg.sender,
            dataType: dataType,
            metadataSummary: metadataSummary,
            consentState: consentState,
            storageState: "PRIVATE_PENDING",
            locatorCommitment: bytes32(0),
            version: 1,
            createdAt: nowTs,
            updatedAt: nowTs,
            exists: true
        });

        datasetIds.push(datasetId);
        datasetIndexPlusOne[datasetId] = datasetIds.length;

        _pushDatasetHistory(datasetId);
        emit DatasetRegistered(datasetId, msg.sender);
    }

    function finalizeDataset(string calldata datasetId, bytes32 locatorCommitment) external onlyHospital {
        Dataset storage ds = datasets[datasetId];
        require(ds.exists, "dataset missing");
        require(_eq(ds.storageState, "PRIVATE_PENDING"), "dataset not pending");
        require(locatorCommitment != bytes32(0), "commitment required");

        ds.storageState = "PRIVATE_READY";
        ds.locatorCommitment = locatorCommitment;
        ds.version += 1;
        ds.updatedAt = uint64(block.timestamp);
        _pushDatasetHistory(datasetId);
        emit DatasetFinalized(datasetId, locatorCommitment);
    }

    function cancelPendingDatasetRegistration(string calldata datasetId) external onlyHospital {
        Dataset storage ds = datasets[datasetId];
        require(ds.exists, "dataset missing");
        require(_eq(ds.storageState, "PRIVATE_PENDING"), "dataset not pending");
        uint256 index = datasetIndexPlusOne[datasetId] - 1;
        uint256 last = datasetIds.length - 1;
        if (index != last) {
            string memory movedId = datasetIds[last];
            datasetIds[index] = movedId;
            datasetIndexPlusOne[movedId] = index + 1;
        }
        datasetIds.pop();
        delete datasetIndexPlusOne[datasetId];
        delete datasets[datasetId];
    }

    function updateConsent(string calldata datasetId, string calldata consentState) external onlyHospital {
        Dataset storage ds = datasets[datasetId];
        require(ds.exists, "dataset missing");
        require(_validConsent(consentState), "invalid consent");
        ds.consentState = consentState;
        ds.version += 1;
        ds.updatedAt = uint64(block.timestamp);
        _pushDatasetHistory(datasetId);
        emit DatasetConsentUpdated(datasetId, consentState);
    }

    function datasetExists(string calldata datasetId) external view returns (bool) {
        return datasets[datasetId].exists;
    }

    function recordDatasetMigration(string calldata datasetId, uint256 sourceChainId, address sourceContract) external onlyHospital {
        require(datasets[datasetId].exists && _eq(datasets[datasetId].storageState, "PRIVATE_READY"), "dataset not ready");
        require(sourceContract != address(0) && migrationSource[datasetId] == bytes32(0), "invalid migration");
        migrationSource[datasetId] = keccak256(abi.encode(sourceChainId, sourceContract));
        emit DatasetMigrationRecorded(datasetId, sourceChainId, sourceContract);
    }

    function getDataset(string calldata datasetId) external view returns (Dataset memory) {
        require(datasets[datasetId].exists, "dataset missing");
        return datasets[datasetId];
    }

    function datasetCount() external view returns (uint256) {
        return datasetIds.length;
    }

    function getDatasetPage(uint256 offset, uint256 limit) external view returns (Dataset[] memory) {
        require(limit > 0 && limit <= 100, "page limit out of range");
        if (offset >= datasetIds.length) return new Dataset[](0);
        uint256 end = offset + limit;
        if (end > datasetIds.length) end = datasetIds.length;
        Dataset[] memory out = new Dataset[](end - offset);
        for (uint256 i = offset; i < end; i++) {
            out[i - offset] = datasets[datasetIds[i]];
        }
        return out;
    }

    function datasetHistoryCount(string calldata datasetId) external view returns (uint256) {
        return datasetHistory[datasetId].length;
    }

    function getDatasetHistoryPage(string calldata datasetId, uint256 offset, uint256 limit) external view returns (Dataset[] memory) {
        require(limit > 0 && limit <= 100, "page limit out of range");
        Dataset[] storage records = datasetHistory[datasetId];
        if (offset >= records.length) return new Dataset[](0);
        uint256 end = offset + limit;
        if (end > records.length) end = records.length;
        Dataset[] memory out = new Dataset[](end - offset);
        for (uint256 i = offset; i < end; i++) out[i - offset] = records[i];
        return out;
    }

    function requestAccessBySig(
        string calldata requestId,
        string calldata datasetId,
        string calldata purpose,
        address claimedResearcher,
        bytes calldata researcherSignature
    ) external {
        require(_validOpaqueId(requestId, "req-"), "opaque requestId required");
        require(_validSha256Commitment(purpose), "purpose commitment required");
        require(!requests[requestId].exists, "request exists");

        Dataset storage ds = datasets[datasetId];
        require(ds.exists, "dataset missing");
        require(_eq(ds.consentState, "ACTIVE"), "dataset not active");
        require(_eq(ds.storageState, "PRIVATE_READY"), "dataset storage not ready");

        address researcher = _recoverSigner(
            researcherRequestDigest(requestId, datasetId, purpose),
            researcherSignature
        );
        require(researcher == claimedResearcher, "researcher signature mismatch");
        require(researcher != hospital, "hospital cannot request");

        requests[requestId] = AccessRequest({
            requestId: requestId,
            datasetId: datasetId,
            requester: researcher,
            purpose: purpose,
            status: "PENDING",
            requestedAt: uint64(block.timestamp),
            decidedAt: 0,
            decidedBy: address(0),
            exists: true
        });

        _pushAccessHistory(requestId);
        emit AccessRequested(requestId, datasetId, researcher);
    }

    function getAccessRequest(string calldata requestId) external view returns (AccessRequest memory) {
        require(requests[requestId].exists, "request missing");
        return requests[requestId];
    }

    function decideAccess(string calldata requestId, string calldata decision) external onlyHospital {
        AccessRequest storage req = requests[requestId];
        require(req.exists, "request missing");

        if (_eq(req.status, "PENDING")) {
            require(_eq(decision, "APPROVED") || _eq(decision, "REJECTED"), "invalid pending transition");
        } else if (_eq(req.status, "APPROVED")) {
            require(_eq(decision, "REVOKED"), "approved request can only be revoked");
        } else {
            revert("request already terminal");
        }

        req.status = decision;
        req.decidedAt = uint64(block.timestamp);
        req.decidedBy = msg.sender;
        _pushAccessHistory(requestId);
        emit AccessDecisionRecorded(requestId, decision, msg.sender);
    }

    function canAccess(string memory requestId) public view returns (bool) {
        AccessRequest storage req = requests[requestId];
        if (!req.exists || !_eq(req.status, "APPROVED")) return false;

        Dataset storage ds = datasets[req.datasetId];
        return ds.exists &&
            _eq(ds.consentState, "ACTIVE") &&
            _eq(ds.storageState, "PRIVATE_READY");
    }

    function getAccessHistoryPage(string calldata requestId, uint256 offset, uint256 limit) external view returns (AccessRequest[] memory) {
        require(limit > 0 && limit <= 100, "page limit out of range");
        AccessRequest[] storage records = accessHistory[requestId];
        if (offset >= records.length) return new AccessRequest[](0);
        uint256 end = offset + limit;
        if (end > records.length) end = records.length;
        AccessRequest[] memory out = new AccessRequest[](end - offset);
        for (uint256 i = offset; i < end; i++) out[i - offset] = records[i];
        return out;
    }

    function _rotationKey(string memory datasetId, uint256 version) private pure returns (bytes32) {
        return keccak256(abi.encodePacked(datasetId, ":", version));
    }

    function recordKeyRotation(
        string calldata datasetId,
        uint256 previousVersion,
        uint256 newVersion
    ) external onlyHospital {
        require(datasets[datasetId].exists, "dataset missing");
        require(newVersion > previousVersion, "invalid rotation versions");
        bytes32 key = _rotationKey(datasetId, newVersion);
        require(!keyRotations[key].exists, "rotation exists");

        keyRotations[key] = KeyRotationRecord({
            datasetId: datasetId,
            previousKeyVersion: previousVersion,
            newKeyVersion: newVersion,
            owner: msg.sender,
            status: "COMMITTED",
            rotatedAt: uint64(block.timestamp),
            exists: true
        });

        emit DatasetKeyRotated(datasetId, previousVersion, newVersion);
    }

    function replaceDatasetCiphertext(
        string calldata datasetId,
        bytes32 expectedCommitment,
        bytes32 newCommitment,
        uint256 previousKeyVersion,
        uint256 newKeyVersion
    ) external onlyHospital {
        Dataset storage ds = datasets[datasetId];
        require(ds.exists && _eq(ds.storageState, "PRIVATE_READY"), "dataset not ready");
        require(ds.locatorCommitment == expectedCommitment, "locator changed");
        require(newCommitment != bytes32(0) && newCommitment != expectedCommitment, "invalid replacement commitment");
        require(newKeyVersion == previousKeyVersion + 1, "invalid replacement key version");
        bytes32 key = _rotationKey(datasetId, newKeyVersion);
        require(!keyRotations[key].exists, "rotation exists");
        keyRotations[key] = KeyRotationRecord({
            datasetId: datasetId,
            previousKeyVersion: previousKeyVersion,
            newKeyVersion: newKeyVersion,
            owner: msg.sender,
            status: "REENCRYPTED",
            rotatedAt: uint64(block.timestamp),
            exists: true
        });
        ds.locatorCommitment = newCommitment;
        ds.version += 1;
        ds.updatedAt = uint64(block.timestamp);
        _pushDatasetHistory(datasetId);
        emit DatasetKeyRotated(datasetId, previousKeyVersion, newKeyVersion);
        emit DatasetCiphertextReplaced(datasetId, expectedCommitment, newCommitment, newKeyVersion);
    }

    function keyRotationExists(string calldata datasetId, uint256 version) external view returns (bool) {
        return keyRotations[_rotationKey(datasetId, version)].exists;
    }

    function getKeyRotation(string calldata datasetId, uint256 version) external view returns (KeyRotationRecord memory) {
        KeyRotationRecord memory rec = keyRotations[_rotationKey(datasetId, version)];
        require(rec.exists, "rotation missing");
        return rec;
    }

    function registerHEJob(
        string calldata jobId,
        string calldata datasetId,
        string calldata requestId,
        string calldata secondaryDatasetId,
        string calldata secondaryRequestId,
        string calldata metric,
        uint256 cohortSize,
        string calldata ciphertextLocatorCommitment,
        string calldata ciphertextManifestSha256
    ) external onlyHospital {
        require(!heJobs[jobId].exists, "HE job exists");
        require(bytes(jobId).length > 0, "jobId required");
        require(cohortSize > 0, "cohort size required");
        require(_validHEMetricDescriptor(metric), "invalid HE metric descriptor");
        require(_validSha256Commitment(ciphertextLocatorCommitment), "HE locator commitment required");

        AccessRequest storage req = requests[requestId];
        require(req.exists, "request missing");
        require(_eq(req.datasetId, datasetId), "request dataset mismatch");
        require(_eq(req.status, "APPROVED"), "request not approved");
        require(canAccess(requestId), "access not active");

        if (bytes(secondaryDatasetId).length > 0 || bytes(secondaryRequestId).length > 0) {
            require(bytes(secondaryDatasetId).length > 0, "secondary dataset required");
            require(bytes(secondaryRequestId).length > 0, "secondary request required");
            AccessRequest storage secondaryReq = requests[secondaryRequestId];
            require(secondaryReq.exists, "secondary request missing");
            require(_eq(secondaryReq.datasetId, secondaryDatasetId), "secondary request dataset mismatch");
            require(secondaryReq.requester == req.requester, "secondary requester mismatch");
            require(canAccess(secondaryRequestId), "secondary access not active");
        }

        heJobs[jobId] = HEJobRecord({
            jobId: jobId,
            datasetId: datasetId,
            requestId: requestId,
            secondaryDatasetId: secondaryDatasetId,
            secondaryRequestId: secondaryRequestId,
            metric: metric,
            cohortSize: cohortSize,
            ciphertextLocatorCommitment: ciphertextLocatorCommitment,
            ciphertextManifestSha256: ciphertextManifestSha256,
            resultLocatorCommitment: "",
            resultSha256: "",
            owner: msg.sender,
            researcher: req.requester,
            status: "ENCRYPTED",
            createdAt: uint64(block.timestamp),
            computedAt: 0,
            decryptedAt: 0,
            exists: true
        });

        _pushHEHistory(jobId);
        emit HEJobRegistered(jobId, datasetId, req.requester);
    }

    function getHEJob(string calldata jobId) external view returns (HEJobRecord memory) {
        require(heJobs[jobId].exists, "HE job missing");
        return heJobs[jobId];
    }

    function recordHEComputationBySig(
        string calldata jobId,
        string calldata resultLocatorCommitment,
        string calldata resultSha256,
        bytes calldata researcherSignature
    ) external {
        HEJobRecord storage job = heJobs[jobId];
        require(job.exists, "HE job missing");
        require(_validSha256Commitment(resultLocatorCommitment), "HE result locator commitment required");
        require(_eq(job.status, "ENCRYPTED"), "HE job not encrypted");
        require(canAccess(job.requestId), "access no longer active");
        if (bytes(job.secondaryRequestId).length > 0) {
            require(canAccess(job.secondaryRequestId), "secondary access no longer active");
        }

        address researcher = _recoverSigner(
            heComputeDigest(jobId),
            researcherSignature
        );
        require(researcher == job.researcher, "approved researcher signature required");

        job.resultLocatorCommitment = resultLocatorCommitment;
        job.resultSha256 = resultSha256;
        job.status = "COMPUTED";
        job.computedAt = uint64(block.timestamp);
        _pushHEHistory(jobId);
        emit HEComputationRecorded(jobId, resultLocatorCommitment, resultSha256);
    }

    function recordHEDecryption(string calldata jobId) external onlyHospital {
        HEJobRecord storage job = heJobs[jobId];
        require(job.exists, "HE job missing");
        require(_eq(job.status, "COMPUTED"), "HE job not computed");
        require(canAccess(job.requestId), "access no longer active");
        if (bytes(job.secondaryRequestId).length > 0) {
            require(canAccess(job.secondaryRequestId), "secondary access no longer active");
        }

        job.status = "DECRYPTED";
        job.decryptedAt = uint64(block.timestamp);
        _pushHEHistory(jobId);
        emit HEDecryptionRecorded(jobId);
    }

    function getHEJobHistoryPage(string calldata jobId, uint256 offset, uint256 limit) external view returns (HEJobRecord[] memory) {
        require(limit > 0 && limit <= 100, "page limit out of range");
        HEJobRecord[] storage records = heJobHistory[jobId];
        if (offset >= records.length) return new HEJobRecord[](0);
        uint256 end = offset + limit;
        if (end > records.length) end = records.length;
        HEJobRecord[] memory out = new HEJobRecord[](end - offset);
        for (uint256 i = offset; i < end; i++) out[i - offset] = records[i];
        return out;
    }
}
