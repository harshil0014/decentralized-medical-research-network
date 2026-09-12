package medicalregistry

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"example.com/medical-registry-chaincode/internal/validation"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

const (
	DatasetPrefix = "DATASET_"
	AccessPrefix  = "ACCESS_"
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

func txTimeUTC(ctx contractapi.TransactionContextInterface) (string, error) {
	ts, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		return "", fmt.Errorf("failed to get transaction timestamp: %w", err)
	}
	return ts.AsTime().UTC().Format(time.RFC3339), nil
}
func datasetKey(id string) string { return DatasetPrefix + id }
func accessKey(id string) string  { return AccessPrefix + id }

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
