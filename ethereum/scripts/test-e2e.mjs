import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { ethers } from "ethers";

const here = path.dirname(new URL(import.meta.url).pathname);
const root = path.resolve(here, "..");
const deployment = JSON.parse(fs.readFileSync(path.join(root, "deployment.json"), "utf8"));
const abi = JSON.parse(fs.readFileSync(path.join(root, "build", "MedicalResearchRegistry.abi.json"), "utf8"));
const provider = new ethers.JsonRpcProvider(deployment.rpcUrl);
const hospital = await provider.getSigner(0);
const researcher = await provider.getSigner(1);
const hospitalContract = new ethers.Contract(deployment.contractAddress, abi, hospital);
const researcherContract = hospitalContract.connect(researcher);

async function sendTx(contract, method, ...args) {
  const fn = contract.getFunction(method);
  const estimated = await fn.estimateGas(...args);
  const tx = await fn(...args, { gasLimit: estimated * 2n });
  return tx.wait();
}

const suffix = Date.now().toString(36);
const datasetId = "SOL-E2E-" + suffix;
const requestId = "REQ-E2E-" + suffix;
const jobId = "JOB-E2E-" + suffix;
const fakeSha = "a".repeat(64);
const fakeResultSha = "b".repeat(64);
const metadataCommitment = "sha256:" + "c".repeat(64);
const purposeCommitment = "sha256:" + "d".repeat(64);
const commitment = ethers.keccak256(ethers.toUtf8Bytes("bafy-demo:" + fakeSha));

const hBalance = await provider.getBalance(await hospital.getAddress());
const rBalance = await provider.getBalance(await researcher.getAddress());
assert(hBalance > ethers.parseEther("90"));
assert(rBalance > ethers.parseEther("90"));

await sendTx(hospitalContract, "registerDataset", datasetId, "LAB_CSV", metadataCommitment, "ACTIVE");
let ds = await hospitalContract.getDataset(datasetId);
assert.equal(ds.storageState, "PRIVATE_PENDING");

await sendTx(hospitalContract, "finalizeDataset", datasetId, commitment);
ds = await hospitalContract.getDataset(datasetId);
assert.equal(ds.storageState, "PRIVATE_READY");

let unauthorized = false;
try {
  await sendTx(researcherContract, "updateConsent", datasetId, "REVOKED");
} catch (_) {
  unauthorized = true;
}
assert.equal(unauthorized, true);

await sendTx(researcherContract, "requestAccess", requestId, datasetId, purposeCommitment);
let req = await hospitalContract.getAccessRequest(requestId);
assert.equal(req.status, "PENDING");

await sendTx(hospitalContract, "decideAccess", requestId, "APPROVED");
assert.equal(await hospitalContract.canAccess(requestId), true);

await sendTx(hospitalContract, "recordKeyRotation", datasetId, 1, 2);
const rotation = await hospitalContract.getKeyRotation(datasetId, 2);
assert.equal(rotation.status, "COMMITTED");

await sendTx(
  hospitalContract,
  "registerHEJob",
  jobId,
  datasetId,
  requestId,
  "",
  "",
  "glucose_mg_dl",
  4,
  "bafy-ciphertext",
  fakeSha
);

let job = await hospitalContract.getHEJob(jobId);
assert.equal(job.status, "ENCRYPTED");
assert.equal(job.researcher.toLowerCase(), (await researcher.getAddress()).toLowerCase());

await sendTx(researcherContract, "recordHEComputation", jobId, "bafy-result", fakeResultSha);
job = await hospitalContract.getHEJob(jobId);
assert.equal(job.status, "COMPUTED");

await sendTx(hospitalContract, "recordHEDecryption", jobId);
job = await hospitalContract.getHEJob(jobId);
assert.equal(job.status, "DECRYPTED");

const revokedJobId = jobId + "-REVOKE";
await sendTx(
  hospitalContract,
  "registerHEJob",
  revokedJobId,
  datasetId,
  requestId,
  "",
  "",
  "glucose_mg_dl",
  4,
  "bafy-ciphertext-revoke",
  fakeSha
);
await sendTx(
  researcherContract,
  "recordHEComputation",
  revokedJobId,
  "bafy-result-revoke",
  fakeResultSha
);

const dHist = await hospitalContract.getDatasetHistory(datasetId);
const aHist = await hospitalContract.getAccessHistory(requestId);
const hHist = await hospitalContract.getHEJobHistory(jobId);
assert(dHist.length >= 2);
assert(aHist.length >= 2);
assert(hHist.length >= 3);

await sendTx(hospitalContract, "decideAccess", requestId, "REVOKED");
assert.equal(await hospitalContract.canAccess(requestId), false);

let decryptAfterRevokeBlocked = false;
try {
  await sendTx(hospitalContract, "recordHEDecryption", revokedJobId);
} catch (_) {
  decryptAfterRevokeBlocked = true;
}
assert.equal(decryptAfterRevokeBlocked, true);

await sendTx(hospitalContract, "updateConsent", datasetId, "REVOKED");
assert.equal(await hospitalContract.canAccess(requestId), false);

console.log("GANACHE ACCOUNTS: >90 test ETH each");
console.log("SOLIDITY GOVERNANCE E2E: PASS");
