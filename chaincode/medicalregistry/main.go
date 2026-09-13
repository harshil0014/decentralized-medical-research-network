package main

import (
	"log"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

func main() {
	cc, err := contractapi.NewChaincode(&SmartContract{})
	if err != nil {
		log.Panicf("failed to create medical registry chaincode: %v", err)
	}

	if err := cc.Start(); err != nil {
		log.Panicf("failed to start medical registry chaincode: %v", err)
	}
}
