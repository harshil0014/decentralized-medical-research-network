package main

import (
	"log"

	"example.com/medical-registry-chaincode/chaincode/medicalregistry"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

func main() {
	cc, err := contractapi.NewChaincode(&medicalregistry.SmartContract{})
	if err != nil {
		log.Panicf("failed to create chaincode: %v", err)
	}
	if err := cc.Start(); err != nil {
		log.Panicf("failed to start chaincode: %v", err)
	}
}
