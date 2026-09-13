package main

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"example.com/medical-registry-chaincode/internal/validation"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

const (
	DatasetPrefix     = "DATASET_"
	AccessPrefix      = "ACCESS_"
	KeyRotationPrefix = "KEYROT_"
)

type SmartContract struct {
	contractapi.Contract
}

type DatasetRecord struct {
	DatasetID       string `json:"datasetId"`
	CID             string `json:"cid"`
	SHA256          string `json:"sha256"`
	OwnerOrg        string `json:"ownerOrg"`
	DataType        string `json:"dataType"`
	MetadataSummary string `json:"metadataSummary"`
	ConsentState    string `json:"consentState"`
	Version         int    `json:"version"`
	CreatedAt       string `json:"createdAt"`
	UpdatedAt       string `json:"updatedAt"`
}

type AccessRequest struct {
	RequestID    string `json:"requestId"`
	DatasetID    string `json:"datasetId"`
	RequesterOrg string `json:"requesterOrg"`
	Purpose      string `json:"purpose"`
	Status       string `json:"status"`
	RequestedAt  string `json:"requestedAt"`
	DecidedAt    string `json:"decidedAt"`
	DecidedBy    string `json:"decidedBy"`
}

type KeyRotationRecord struct {
	DatasetID          string `json:"datasetId"`
	PreviousKeyVersion int    `json:"previousKeyVersion"`
	NewKeyVersion      int    `json:"newKeyVersion"`
	OwnerOrg           string `json:"ownerOrg"`
	Status             string `json:"status"`
	RotatedAt          string `json:"rotatedAt"`
}

func txTimeUTC(ctx contractapi.TransactionContextInterface) (string, error) {
	ts, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		return "", fmt.Errorf("failed to get transaction timestamp: %w", err)
	}
	return ts.AsTime().UTC().Format(time.RFC3339), nil
}
func datasetKey(id string) string { return DatasetPrefix + id }
func accessKey(id string) string  { return AccessPrefix + id }

func keyRotationKey(datasetID string, newVersion int) string {
	return fmt.Sprintf(
		"%s%s_%010d",
		KeyRotationPrefix,
		datasetID,
		newVersion,
	)
}

func (s *SmartContract) RegisterDataset(ctx contractapi.TransactionContextInterface,
	datasetID, cid, sha256, dataType, metadataSummary, consentState string) error {

	if strings.TrimSpace(datasetID) == "" {
		return fmt.Errorf("datasetID is required")
	}
	if strings.TrimSpace(cid) == "" {
		return fmt.Errorf("cid is required")
	}
	if !validation.ValidSHA256(sha256) {
		return fmt.Errorf("sha256 must be a 64-character hex value")
	}
	ownerOrg, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP ID: %w", err)
	}

	exists, err := s.DatasetExists(ctx, datasetID)
	if err != nil {
		return err
	}
	if exists {
		return fmt.Errorf("dataset %s already exists", datasetID)
	}

	consent, err := validation.NormalizeConsent(consentState)
	if err != nil {
		return err
	}
	ts, err := txTimeUTC(ctx)

	if err != nil {

		return err

	}
	rec := DatasetRecord{
		DatasetID: datasetID, CID: cid, SHA256: strings.ToLower(sha256),
		OwnerOrg: ownerOrg, DataType: dataType, MetadataSummary: metadataSummary,
		ConsentState: consent, Version: 1, CreatedAt: ts, UpdatedAt: ts,
	}
	b, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if err := ctx.GetStub().PutState(datasetKey(datasetID), b); err != nil {
		return err
	}
	return ctx.GetStub().SetEvent("DatasetRegistered", b)
}

func (s *SmartContract) ReadDataset(ctx contractapi.TransactionContextInterface, datasetID string) (*DatasetRecord, error) {
	b, err := ctx.GetStub().GetState(datasetKey(datasetID))
	if err != nil {
		return nil, err
	}
	if b == nil {
		return nil, fmt.Errorf("dataset %s does not exist", datasetID)
	}
	var rec DatasetRecord
	if err := json.Unmarshal(b, &rec); err != nil {
		return nil, err
	}
	return &rec, nil
}

func (s *SmartContract) DatasetExists(ctx contractapi.TransactionContextInterface, datasetID string) (bool, error) {
	b, err := ctx.GetStub().GetState(datasetKey(datasetID))
	if err != nil {
		return false, err
	}
	return b != nil, nil
}

func parsePositiveKeyVersion(
	raw string,
	field string,
) (int, error) {
	value, err := strconv.Atoi(
		strings.TrimSpace(raw),
	)

	if err != nil || value < 1 {
		return 0, fmt.Errorf(
			"%s must be a positive integer",
			field,
		)
	}

	return value, nil
}

func (s *SmartContract) KeyRotationExists(
	ctx contractapi.TransactionContextInterface,
	datasetID string,
	newKeyVersionRaw string,
) (bool, error) {

	newVersion, err := parsePositiveKeyVersion(
		newKeyVersionRaw,
		"newKeyVersion",
	)

	if err != nil {
		return false, err
	}

	b, err := ctx.GetStub().GetState(
		keyRotationKey(
			datasetID,
			newVersion,
		),
	)

	if err != nil {
		return false, err
	}

	return b != nil, nil
}

func (s *SmartContract) ReadKeyRotation(
	ctx contractapi.TransactionContextInterface,
	datasetID string,
	newKeyVersionRaw string,
) (*KeyRotationRecord, error) {

	newVersion, err := parsePositiveKeyVersion(
		newKeyVersionRaw,
		"newKeyVersion",
	)

	if err != nil {
		return nil, err
	}

	b, err := ctx.GetStub().GetState(
		keyRotationKey(
			datasetID,
			newVersion,
		),
	)

	if err != nil {
		return nil, err
	}

	if b == nil {
		return nil, fmt.Errorf(
			"key rotation for dataset %s version %d does not exist",
			datasetID,
			newVersion,
		)
	}

	var record KeyRotationRecord

	if err := json.Unmarshal(
		b,
		&record,
	); err != nil {
		return nil, err
	}

	return &record, nil
}

func (s *SmartContract) RecordKeyRotation(
	ctx contractapi.TransactionContextInterface,
	datasetID string,
	previousKeyVersionRaw string,
	newKeyVersionRaw string,
) error {

	if strings.TrimSpace(datasetID) == "" {
		return fmt.Errorf(
			"datasetID is required",
		)
	}

	previousVersion, err := parsePositiveKeyVersion(
		previousKeyVersionRaw,
		"previousKeyVersion",
	)

	if err != nil {
		return err
	}

	newVersion, err := parsePositiveKeyVersion(
		newKeyVersionRaw,
		"newKeyVersion",
	)

	if err != nil {
		return err
	}

	if newVersion != previousVersion+1 {
		return fmt.Errorf(
			"new key version must be exactly previous version + 1",
		)
	}

	dataset, err := s.ReadDataset(
		ctx,
		datasetID,
	)

	if err != nil {
		return err
	}

	callerOrg, err := ctx.GetClientIdentity().GetMSPID()

	if err != nil {
		return fmt.Errorf(
			"failed to read caller MSP ID: %w",
			err,
		)
	}

	if callerOrg != dataset.OwnerOrg {
		return fmt.Errorf(
			"only dataset owner organisation %s can record key rotation",
			dataset.OwnerOrg,
		)
	}

	key := keyRotationKey(
		datasetID,
		newVersion,
	)

	existing, err := ctx.GetStub().GetState(
		key,
	)

	if err != nil {
		return err
	}

	if existing != nil {
		return fmt.Errorf(
			"key rotation for dataset %s version %d already exists",
			datasetID,
			newVersion,
		)
	}

	rotatedAt, err := txTimeUTC(
		ctx,
	)

	if err != nil {
		return err
	}

	record := KeyRotationRecord{
		DatasetID:          datasetID,
		PreviousKeyVersion: previousVersion,
		NewKeyVersion:      newVersion,
		OwnerOrg:           dataset.OwnerOrg,
		Status:             "COMMITTED",
		RotatedAt:          rotatedAt,
	}

	b, err := json.Marshal(
		record,
	)

	if err != nil {
		return err
	}

	if err := ctx.GetStub().PutState(
		key,
		b,
	); err != nil {
		return err
	}

	return ctx.GetStub().SetEvent(
		"DatasetKeyRotated",
		b,
	)
}

func (s *SmartContract) UpdateConsent(ctx contractapi.TransactionContextInterface, datasetID, consentState string) error {
	rec, err := s.ReadDataset(ctx, datasetID)
	if err != nil {
		return err
	}
	callerOrg, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP ID: %w", err)
	}
	if callerOrg != rec.OwnerOrg {
		return fmt.Errorf("only owner organisation %s can update consent", rec.OwnerOrg)
	}
	consent, err := validation.NormalizeConsent(consentState)
	if err != nil {
		return err
	}
	rec.ConsentState = consent
	rec.Version++
	updatedAt, err := txTimeUTC(ctx)

	if err != nil {

		return err

	}

	rec.UpdatedAt = updatedAt
	b, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if err := ctx.GetStub().PutState(datasetKey(datasetID), b); err != nil {
		return err
	}
	return ctx.GetStub().SetEvent("DatasetConsentUpdated", b)
}

func (s *SmartContract) RequestAccess(ctx contractapi.TransactionContextInterface,
	requestID, datasetID, purpose string) error {

	if strings.TrimSpace(requestID) == "" {
		return fmt.Errorf("requestID is required")
	}
	if strings.TrimSpace(purpose) == "" {
		return fmt.Errorf("purpose is required")
	}
	requesterOrg, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP ID: %w", err)
	}
	ds, err := s.ReadDataset(ctx, datasetID)
	if err != nil {
		return err
	}
	if ds.ConsentState != "ACTIVE" {
		return fmt.Errorf("dataset %s is not available for new access requests", datasetID)
	}

	existing, err := ctx.GetStub().GetState(accessKey(requestID))
	if err != nil {
		return err
	}
	if existing != nil {
		return fmt.Errorf("access request %s already exists", requestID)
	}

	requestedAt, err := txTimeUTC(ctx)

	if err != nil {

		return err

	}

	req := AccessRequest{
		RequestID: requestID, DatasetID: datasetID, RequesterOrg: requesterOrg,
		Purpose: purpose, Status: "PENDING", RequestedAt: requestedAt,
	}
	b, err := json.Marshal(req)
	if err != nil {
		return err
	}
	if err := ctx.GetStub().PutState(accessKey(requestID), b); err != nil {
		return err
	}
	return ctx.GetStub().SetEvent("AccessRequested", b)
}

func (s *SmartContract) ReadAccessRequest(ctx contractapi.TransactionContextInterface, requestID string) (*AccessRequest, error) {
	b, err := ctx.GetStub().GetState(accessKey(requestID))
	if err != nil {
		return nil, err
	}
	if b == nil {
		return nil, fmt.Errorf("access request %s does not exist", requestID)
	}
	var req AccessRequest
	if err := json.Unmarshal(b, &req); err != nil {
		return nil, err
	}
	return &req, nil
}

func (s *SmartContract) DecideAccess(ctx contractapi.TransactionContextInterface,
	requestID, decision string) error {

	req, err := s.ReadAccessRequest(ctx, requestID)
	if err != nil {
		return err
	}
	ds, err := s.ReadDataset(ctx, req.DatasetID)
	if err != nil {
		return err
	}
	callerOrg, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP ID: %w", err)
	}
	if callerOrg != ds.OwnerOrg {
		return fmt.Errorf("only owner organisation %s can decide access", ds.OwnerOrg)
	}
	normalized, err := validation.NormalizeDecision(decision)
	if err != nil {
		return err
	}

	// Simple prototype state machine:
	// PENDING -> APPROVED or REJECTED; APPROVED -> REVOKED; terminal otherwise.
	switch req.Status {
	case "PENDING":
		if normalized != "APPROVED" && normalized != "REJECTED" {
			return fmt.Errorf("pending request can only be approved or rejected")
		}
	case "APPROVED":
		if normalized != "REVOKED" {
			return fmt.Errorf("approved request can only be revoked")
		}
	case "REJECTED", "REVOKED":
		return fmt.Errorf("access request is already in terminal state %s", req.Status)
	default:
		return fmt.Errorf("unknown access request state %s", req.Status)
	}

	decidedAt, err := txTimeUTC(ctx)

	if err != nil {

		return err

	}

	req.Status = normalized

	req.DecidedAt = decidedAt
	req.DecidedBy = callerOrg
	b, err := json.Marshal(req)
	if err != nil {
		return err
	}
	if err := ctx.GetStub().PutState(accessKey(requestID), b); err != nil {
		return err
	}
	return ctx.GetStub().SetEvent("AccessDecisionRecorded", b)
}

// CanAccess returns true only when the request is APPROVED and dataset consent is ACTIVE.
// The off-chain application will call this before releasing an IPFS object.
func (s *SmartContract) CanAccess(ctx contractapi.TransactionContextInterface, requestID string) (bool, error) {
	req, err := s.ReadAccessRequest(ctx, requestID)
	if err != nil {
		return false, err
	}
	if req.Status != "APPROVED" {
		return false, nil
	}
	ds, err := s.ReadDataset(ctx, req.DatasetID)
	if err != nil {
		return false, err
	}
	return ds.ConsentState == "ACTIVE", nil
}

func (s *SmartContract) GetAccessHistory(ctx contractapi.TransactionContextInterface, requestID string) ([]map[string]interface{}, error) {
	iter, err := ctx.GetStub().GetHistoryForKey(accessKey(requestID))
	if err != nil {
		return nil, err
	}
	defer iter.Close()
	var out []map[string]interface{}
	for iter.HasNext() {
		item, err := iter.Next()
		if err != nil {
			return nil, err
		}
		row := map[string]interface{}{
			"txId":      item.TxId,
			"timestamp": item.Timestamp.AsTime().UTC().Format(time.RFC3339),
			"isDelete":  item.IsDelete,
		}
		if !item.IsDelete && len(item.Value) > 0 {
			var value interface{}
			if json.Unmarshal(item.Value, &value) == nil {
				row["value"] = value
			}
		}
		out = append(out, row)
	}
	return out, nil
}

func (s *SmartContract) GetAllDatasets(ctx contractapi.TransactionContextInterface) ([]*DatasetRecord, error) {
	iter, err := ctx.GetStub().GetStateByRange(DatasetPrefix, DatasetPrefix+"~")
	if err != nil {
		return nil, err
	}
	defer iter.Close()
	var out []*DatasetRecord
	for iter.HasNext() {
		kv, err := iter.Next()
		if err != nil {
			return nil, err
		}
		var rec DatasetRecord
		if err := json.Unmarshal(kv.Value, &rec); err != nil {
			return nil, err
		}
		out = append(out, &rec)
	}
	return out, nil
}

func (s *SmartContract) GetDatasetHistory(ctx contractapi.TransactionContextInterface, datasetID string) ([]map[string]interface{}, error) {
	iter, err := ctx.GetStub().GetHistoryForKey(datasetKey(datasetID))
	if err != nil {
		return nil, err
	}
	defer iter.Close()
	var out []map[string]interface{}
	for iter.HasNext() {
		item, err := iter.Next()
		if err != nil {
			return nil, err
		}
		row := map[string]interface{}{
			"txId":      item.TxId,
			"timestamp": item.Timestamp.AsTime().UTC().Format(time.RFC3339),
			"isDelete":  item.IsDelete,
		}
		if !item.IsDelete && len(item.Value) > 0 {
			var value interface{}
			if json.Unmarshal(item.Value, &value) == nil {
				row["value"] = value
			}
		}
		out = append(out, row)
	}
	return out, nil
}

// -----------------------------------------------------------------------------
// Homomorphic-encryption research provenance
//
// IMPORTANT:
// This ledger record stores only hashes and workflow metadata.
// It never stores plaintext medical values, decrypted aggregates,
// Microsoft SEAL secret keys, or raw ciphertext bytes.
// -----------------------------------------------------------------------------

const HEJobPrefix = "HEJOB_"

type HEJobRecord struct {
	JobID                    string `json:"jobId"`
	DatasetID                string `json:"datasetId"`
	RequestID                string `json:"requestId"`
	Metric                   string `json:"metric"`
	CohortSize               int    `json:"cohortSize"`
	CiphertextCID            string `json:"ciphertextCid"`
	CiphertextManifestSHA256 string `json:"ciphertextManifestSha256"`
	ResultCID                string `json:"resultCid"`
	ResultSHA256             string `json:"resultSha256"`
	OwnerOrg                 string `json:"ownerOrg"`
	ResearcherOrg            string `json:"researcherOrg"`
	Status                   string `json:"status"`
	CreatedAt                string `json:"createdAt"`
	ComputedAt               string `json:"computedAt"`
	DecryptedAt              string `json:"decryptedAt"`
}

func heJobKey(id string) string {
	return HEJobPrefix + id
}

// RegisterHEJob is called by the hospital after creating the encrypted cohort.
// Only a SHA-256 manifest of the ciphertext set is placed on-chain.
func (s *SmartContract) RegisterHEJob(
	ctx contractapi.TransactionContextInterface,
	jobID string,
	datasetID string,
	requestID string,
	metric string,
	cohortSizeRaw string,
	ciphertextCID string,
	ciphertextManifestSHA256 string,
) error {

	if strings.TrimSpace(jobID) == "" {
		return fmt.Errorf("jobID is required")
	}

	if strings.TrimSpace(datasetID) == "" {
		return fmt.Errorf("datasetID is required")
	}

	if strings.TrimSpace(requestID) == "" {
		return fmt.Errorf("requestID is required")
	}

	if strings.TrimSpace(metric) == "" {
		return fmt.Errorf("metric is required")
	}

	cohortSize, err := strconv.Atoi(cohortSizeRaw)
	if err != nil || cohortSize < 1 {
		return fmt.Errorf("cohortSize must be a positive integer")
	}

	if strings.TrimSpace(ciphertextCID) == "" {
		return fmt.Errorf("ciphertext CID is required")
	}

	if !validation.ValidSHA256(ciphertextManifestSHA256) {
		return fmt.Errorf(
			"ciphertext manifest SHA256 must be a 64-character hex value",
		)
	}

	existing, err := ctx.GetStub().GetState(
		heJobKey(jobID),
	)
	if err != nil {
		return err
	}

	if existing != nil {
		return fmt.Errorf(
			"HE job %s already exists",
			jobID,
		)
	}

	// HE jobs are only valid when backed by a real,
	// approved Fabric access request.
	req, err := s.ReadAccessRequest(
		ctx,
		requestID,
	)
	if err != nil {
		return err
	}

	if req.DatasetID != datasetID {
		return fmt.Errorf(
			"access request %s belongs to dataset %s, not %s",
			requestID,
			req.DatasetID,
			datasetID,
		)
	}

	if req.Status != "APPROVED" {
		return fmt.Errorf(
			"access request %s is not approved",
			requestID,
		)
	}

	ds, err := s.ReadDataset(
		ctx,
		datasetID,
	)
	if err != nil {
		return err
	}

	if ds.ConsentState != "ACTIVE" {
		return fmt.Errorf(
			"dataset %s consent is not active",
			datasetID,
		)
	}

	callerOrg, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf(
			"failed to read caller MSP ID: %w",
			err,
		)
	}

	// Hospital/owner creates the encrypted research job.
	if callerOrg != ds.OwnerOrg {
		return fmt.Errorf(
			"only dataset owner organisation %s can register HE job",
			ds.OwnerOrg,
		)
	}

	createdAt, err := txTimeUTC(ctx)
	if err != nil {
		return err
	}

	record := HEJobRecord{
		JobID:                    jobID,
		DatasetID:                datasetID,
		RequestID:                requestID,
		Metric:                   metric,
		CohortSize:               cohortSize,
		CiphertextCID:            strings.TrimSpace(ciphertextCID),
		CiphertextManifestSHA256: strings.ToLower(ciphertextManifestSHA256),
		ResultCID:                "",
		ResultSHA256:             "",
		OwnerOrg:                 ds.OwnerOrg,
		ResearcherOrg:            req.RequesterOrg,
		Status:                   "ENCRYPTED",
		CreatedAt:                createdAt,
		ComputedAt:               "",
		DecryptedAt:              "",
	}

	b, err := json.Marshal(record)
	if err != nil {
		return err
	}

	if err := ctx.GetStub().PutState(
		heJobKey(jobID),
		b,
	); err != nil {
		return err
	}

	return ctx.GetStub().SetEvent(
		"HEJobRegistered",
		b,
	)
}

func (s *SmartContract) ReadHEJob(
	ctx contractapi.TransactionContextInterface,
	jobID string,
) (*HEJobRecord, error) {

	b, err := ctx.GetStub().GetState(heJobKey(jobID))
	if err != nil {
		return nil, err
	}

	if b == nil {
		return nil, fmt.Errorf("HE job %s does not exist", jobID)
	}

	var record HEJobRecord

	if err := json.Unmarshal(b, &record); err != nil {
		return nil, err
	}

	return &record, nil
}

// RecordHEComputation is called from the researcher organisation after
// computation finishes. Only the encrypted-result SHA-256 is recorded.
func (s *SmartContract) RecordHEComputation(
	ctx contractapi.TransactionContextInterface,
	jobID string,
	resultCID string,
	resultSHA256 string,
) error {

	if strings.TrimSpace(resultCID) == "" {
		return fmt.Errorf("result CID is required")
	}

	if !validation.ValidSHA256(resultSHA256) {
		return fmt.Errorf("result SHA256 must be a 64-character hex value")
	}

	record, err := s.ReadHEJob(ctx, jobID)
	if err != nil {
		return err
	}

	if record.Status != "ENCRYPTED" {
		return fmt.Errorf(
			"HE job %s must be ENCRYPTED before computation; current state is %s",
			jobID,
			record.Status,
		)
	}

	// Re-check authorization at computation time.
	// Approval may have been revoked, or dataset consent may have changed
	// after the HE job was originally registered.
	allowed, err := s.CanAccess(
		ctx,
		record.RequestID,
	)
	if err != nil {
		return err
	}

	if !allowed {
		return fmt.Errorf(
			"access request %s is no longer authorized for HE computation",
			record.RequestID,
		)
	}

	researcherOrg, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP ID: %w", err)
	}

	if researcherOrg != record.ResearcherOrg {
		return fmt.Errorf(
			"only approved researcher organisation %s can record computation",
			record.ResearcherOrg,
		)
	}

	computedAt, err := txTimeUTC(ctx)
	if err != nil {
		return err
	}

	record.ResultCID = strings.TrimSpace(resultCID)
	record.ResultSHA256 = strings.ToLower(resultSHA256)
	record.ResearcherOrg = researcherOrg
	record.Status = "COMPUTED"
	record.ComputedAt = computedAt

	b, err := json.Marshal(record)
	if err != nil {
		return err
	}

	if err := ctx.GetStub().PutState(heJobKey(jobID), b); err != nil {
		return err
	}

	return ctx.GetStub().SetEvent("HEComputationRecorded", b)
}

// RecordHEDecryption records that the hospital decrypted the final result.
// The decrypted result itself is deliberately NOT written to the ledger.
func (s *SmartContract) RecordHEDecryption(
	ctx contractapi.TransactionContextInterface,
	jobID string,
) error {

	record, err := s.ReadHEJob(ctx, jobID)
	if err != nil {
		return err
	}

	if record.Status != "COMPUTED" {
		return fmt.Errorf(
			"HE job %s must be COMPUTED before decryption; current state is %s",
			jobID,
			record.Status,
		)
	}

	callerOrg, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP ID: %w", err)
	}

	if callerOrg != record.OwnerOrg {
		return fmt.Errorf(
			"only owner organisation %s can record decryption",
			record.OwnerOrg,
		)
	}

	decryptedAt, err := txTimeUTC(ctx)
	if err != nil {
		return err
	}

	record.Status = "DECRYPTED"
	record.DecryptedAt = decryptedAt

	b, err := json.Marshal(record)
	if err != nil {
		return err
	}

	if err := ctx.GetStub().PutState(heJobKey(jobID), b); err != nil {
		return err
	}

	return ctx.GetStub().SetEvent("HEDecryptionRecorded", b)
}

func (s *SmartContract) GetHEJobHistory(
	ctx contractapi.TransactionContextInterface,
	jobID string,
) ([]map[string]interface{}, error) {

	iter, err := ctx.GetStub().GetHistoryForKey(heJobKey(jobID))
	if err != nil {
		return nil, err
	}
	defer iter.Close()

	var history []map[string]interface{}

	for iter.HasNext() {
		item, err := iter.Next()
		if err != nil {
			return nil, err
		}

		row := map[string]interface{}{
			"txId":      item.TxId,
			"timestamp": item.Timestamp.AsTime().UTC().Format(time.RFC3339),
			"isDelete":  item.IsDelete,
		}

		if !item.IsDelete && len(item.Value) > 0 {
			var value interface{}

			if err := json.Unmarshal(item.Value, &value); err != nil {
				return nil, err
			}

			row["value"] = value
		}

		history = append(history, row)
	}

	return history, nil
}
