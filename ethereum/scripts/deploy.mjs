import fs from "node:fs";
import path from "node:path";
import { ethers } from "ethers";

const here = path.dirname(new URL(import.meta.url).pathname);
const root = path.resolve(here, "..");
const abi = JSON.parse(fs.readFileSync(path.join(root, "build", "MedicalResearchRegistry.abi.json"), "utf8"));
const bytecode = fs.readFileSync(path.join(root, "build", "MedicalResearchRegistry.bytecode.txt"), "utf8").trim();
const rpcUrl = process.env.ETH_RPC_URL || "http://127.0.0.1:8545";

const provider = new ethers.JsonRpcProvider(rpcUrl);
const hospital = await provider.getSigner(0);
const researcherSigners = [];
for (let index = 1; index <= 9; index += 1) {
  researcherSigners.push(await provider.getSigner(index));
}
const researcherAddresses = await Promise.all(
  researcherSigners.map((signer) => signer.getAddress())
);
const researcher = researcherSigners[0];
const factory = new ethers.ContractFactory(abi, bytecode, hospital);
const contract = await factory.deploy();
await contract.waitForDeployment();

const network = await provider.getNetwork();
const deploymentTx = contract.deploymentTransaction();
const receipt = await deploymentTx.wait();

const deployment = {
  rpcUrl,
  chainId: Number(network.chainId),
  contractAddress: await contract.getAddress(),
  hospitalAddress: await hospital.getAddress(),
  researcherAddress: await researcher.getAddress(),
  researcherAddresses,
  deployedBlock: receipt.blockNumber,
  abiPath: "ethereum/build/MedicalResearchRegistry.abi.json"
};

fs.writeFileSync(
  path.join(root, "deployment.json"),
  JSON.stringify(deployment, null, 2)
);

console.log(JSON.stringify(deployment, null, 2));
console.log("ETHEREUM DEPLOY: PASS");
