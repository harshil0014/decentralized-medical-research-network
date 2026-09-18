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

const suffix = Date.now().toString(36);
const datasetId = "SOL-E2E-" + suffix;
const requestId = "REQ-E2E-" + suffix;
const jobId = "JOB-E2E-" + suffix;
const fakeSha = "a".repeat(64);
const fakeResultSha = "b".repeat(64);
const commitment = ethers.keccak256(ethers.toUtf8Bytes("bafy-demo:" + fakeSha));

const hBalance = await provider.getBalance(await hospital.getAddress());
const rBalance = await provider.getBalance(await researcher.getAddress());
assert(hBalance > ethers.parseEther("90"));
assert(rBalance > ethers.parseEther("90"));

await (await hospitalContract.registerDataset(datasetId, "LAB_CSV", "Synthetic glucose cohort", "ACTIVE")).wait();
let ds = await hospitalContract.getDataset(datasetId);
assert.equal(ds.storageState, "PRIVATE_PENDING");

await (await hospitalContract.finalizeDataset(datasetId, commitment)).wait();
ds = await hospitalContract.getDataset(datasetId);
assert.equal(ds.storageState, "PRIVATE_READY");

let unauthorized = false;
try {
  await (await researcherContract.updateConsent(datasetId, "REVOKED")).wait();
} catch (_) {
  unauthorized = true;
}
assert.equal(unauthorized, true);

await (await researcherContract.requestAccess(requestId, datasetId, "Glucose analysis")).wait();
let req = await hospitalContract.getAccessRequest(requestId);
assert.equal(req.status, "PENDING");

await (await hospitalContract.decideAccess(requestId, "APPROVED")).wait();
assert.equal(await hospitalContract.canAccess(requestId), true);

await (await hospitalContract.recordKeyRotation(datasetId, 1, 2)).wait();
const rotation = await hospitalContract.getKeyRotation(datasetId, 2);
assert.equal(rotation.status, "COMMITTED");

await (await hospitalContract.registerHEJob(
  jobId,
  datasetId,
  requestId,
  "glucose_mg_dl",
  4,
  "bafy-ciphertext",
  fakeSha
)).wait();

let job = await hospitalContract.getHEJob(jobId);
assert.equal(job.status, "ENCRYPTED");
assert.equal(job.researcher.toLowerCase(), (await researcher.getAddress()).toLowerCase());

await (await researcherContract.recordHEComputation(jobId, "bafy-result", fakeResultSha)).wait();
job = await hospitalContract.getHEJob(jobId);
assert.equal(job.status, "COMPUTED");

await (await hospitalContract.recordHEDecryption(jobId)).wait();
job = await hospitalContract.getHEJob(jobId);
assert.equal(job.status, "DECRYPTED");

const dHist = await hospitalContract.getDatasetHistory(datasetId);
const aHist = await hospitalContract.getAccessHistory(requestId);
const hHist = await hospitalContract.getHEJobHistory(jobId);
assert(dHist.length >= 2);
assert(aHist.length >= 2);
assert(hHist.length >= 3);

await (await hospitalContract.decideAccess(requestId, "REVOKED")).wait();
assert.equal(await hospitalContract.canAccess(requestId), false);

await (await hospitalContract.updateConsent(datasetId, "REVOKED")).wait();
assert.equal(await hospitalContract.canAccess(requestId), false);

console.log("GANACHE ACCOUNTS: >90 test ETH each");
console.log("SOLIDITY GOVERNANCE E2E: PASS");
