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
        string ciphertextCid;
        string ciphertextManifestSha256;
        string resultCid;
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
    mapping(string => bool) private datasetSeen;
    string[] private datasetIds;
    mapping(string => Dataset[]) private datasetHistory;

    mapping(string => AccessRequest) private requests;
    mapping(string => AccessRequest[]) private accessHistory;

    mapping(bytes32 => KeyRotationRecord) private keyRotations;

    mapping(string => HEJobRecord) private heJobs;
    mapping(string => HEJobRecord[]) private heJobHistory;

    event DatasetRegistered(string indexed datasetId, address indexed owner);
    event DatasetFinalized(string indexed datasetId, bytes32 locatorCommitment);
    event DatasetConsentUpdated(string indexed datasetId, string consentState);
    event AccessRequested(string indexed requestId, string indexed datasetId, address indexed requester);
    event AccessDecisionRecorded(string indexed requestId, string status, address indexed decidedBy);
    event DatasetKeyRotated(string indexed datasetId, uint256 previousVersion, uint256 newVersion);
    event HEJobRegistered(string indexed jobId, string indexed datasetId, address indexed researcher);
    event HEComputationRecorded(string indexed jobId, string resultCid, string resultSha256);
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
        require(bytes(datasetId).length > 0, "datasetId required");
        require(bytes(dataType).length > 0, "dataType required");
        require(bytes(metadataSummary).length > 0, "metadata required");
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

        if (!datasetSeen[datasetId]) {
            datasetSeen[datasetId] = true;
            datasetIds.push(datasetId);
        }

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

    function getDataset(string calldata datasetId) external view returns (Dataset memory) {
        require(datasets[datasetId].exists, "dataset missing");
        return datasets[datasetId];
    }

    function getAllDatasets() external view returns (Dataset[] memory) {
        uint256 count = 0;
        for (uint256 i = 0; i < datasetIds.length; i++) {
            if (datasets[datasetIds[i]].exists) count++;
        }

        Dataset[] memory out = new Dataset[](count);
        uint256 cursor = 0;
        for (uint256 i = 0; i < datasetIds.length; i++) {
            if (datasets[datasetIds[i]].exists) {
                out[cursor] = datasets[datasetIds[i]];
                cursor++;
            }
        }
        return out;
    }

    function getDatasetHistory(string calldata datasetId) external view returns (Dataset[] memory) {
        return datasetHistory[datasetId];
    }

    function requestAccess(
        string calldata requestId,
        string calldata datasetId,
        string calldata purpose
    ) external {
        require(msg.sender != hospital, "hospital cannot request");
        require(bytes(requestId).length > 0, "requestId required");
        require(bytes(purpose).length > 0, "purpose required");
        require(!requests[requestId].exists, "request exists");

        Dataset storage ds = datasets[datasetId];
        require(ds.exists, "dataset missing");
        require(_eq(ds.consentState, "ACTIVE"), "dataset not active");
        require(_eq(ds.storageState, "PRIVATE_READY"), "dataset storage not ready");

        requests[requestId] = AccessRequest({
            requestId: requestId,
            datasetId: datasetId,
            requester: msg.sender,
            purpose: purpose,
            status: "PENDING",
            requestedAt: uint64(block.timestamp),
            decidedAt: 0,
            decidedBy: address(0),
            exists: true
        });

        _pushAccessHistory(requestId);
        emit AccessRequested(requestId, datasetId, msg.sender);
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

    function getAccessHistory(string calldata requestId) external view returns (AccessRequest[] memory) {
        return accessHistory[requestId];
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
        string calldata ciphertextCid,
        string calldata ciphertextManifestSha256
    ) external onlyHospital {
        require(!heJobs[jobId].exists, "HE job exists");
        require(bytes(jobId).length > 0, "jobId required");
        require(cohortSize > 0, "cohort size required");

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
            ciphertextCid: ciphertextCid,
            ciphertextManifestSha256: ciphertextManifestSha256,
            resultCid: "",
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

    function recordHEComputation(
        string calldata jobId,
        string calldata resultCid,
        string calldata resultSha256
    ) external {
        HEJobRecord storage job = heJobs[jobId];
        require(job.exists, "HE job missing");
        require(msg.sender == job.researcher, "approved researcher only");
        require(_eq(job.status, "ENCRYPTED"), "HE job not encrypted");
        require(canAccess(job.requestId), "access no longer active");
        if (bytes(job.secondaryRequestId).length > 0) {
            require(canAccess(job.secondaryRequestId), "secondary access no longer active");
        }

        job.resultCid = resultCid;
        job.resultSha256 = resultSha256;
        job.status = "COMPUTED";
        job.computedAt = uint64(block.timestamp);
        _pushHEHistory(jobId);
        emit HEComputationRecorded(jobId, resultCid, resultSha256);
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

    function getHEJobHistory(string calldata jobId) external view returns (HEJobRecord[] memory) {
        return heJobHistory[jobId];
    }
}
