import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { ethers } from "ethers";

const here = path.dirname(new URL(import.meta.url).pathname);
const root = path.resolve(here, "..");
const abi = JSON.parse(fs.readFileSync(path.join(root, "build", "MedicalResearchRegistry.abi.json"), "utf8"));
const bytecode = fs.readFileSync(path.join(root, "build", "MedicalResearchRegistry.bytecode.txt"), "utf8").trim();
const sourceSha256 = createHash("sha256").update(
  fs.readFileSync(path.join(root, "contracts", "MedicalResearchRegistry.sol")),
).digest("hex");
const rpcUrl = process.env.ETH_RPC_URL || "http://127.0.0.1:8545";

const provider = new ethers.JsonRpcProvider(rpcUrl);

let hospital;
const hospitalKeyFile = (process.env.MEDICAL_HOSPITAL_PRIVATE_KEY_FILE || "").trim();
if (hospitalKeyFile) {
  const privateKey = fs.readFileSync(hospitalKeyFile, "utf8").trim();
  hospital = new ethers.Wallet(privateKey, provider);
} else {
  hospital = await provider.getSigner(0);
}

const factory = new ethers.ContractFactory(abi, bytecode, hospital);
const contract = await factory.deploy();
await contract.waitForDeployment();

const network = await provider.getNetwork();
const deploymentTx = contract.deploymentTransaction();
const receipt = await deploymentTx.wait();

const deployment = {
  rpcUrl,
  network: process.env.MEDICAL_ETHEREUM_NETWORK || "Ganache",
  chainId: Number(network.chainId),
  contractAddress: await contract.getAddress(),
  hospitalAddress: await hospital.getAddress(),
  researcherSigning: "external-eip191",
  deployedBlock: receipt.blockNumber,
  sourceSha256,
  abiPath: "ethereum/build/MedicalResearchRegistry.abi.json"
};

fs.writeFileSync(
  path.join(root, "deployment.json"),
  JSON.stringify(deployment, null, 2)
);

console.log(JSON.stringify(deployment, null, 2));
console.log("ETHEREUM DEPLOY: PASS");
