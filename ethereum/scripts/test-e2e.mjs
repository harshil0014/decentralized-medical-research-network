import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { ethers } from "ethers";

const here = path.dirname(new URL(import.meta.url).pathname);
const root = path.resolve(here, "..");
const deployment = JSON.parse(fs.readFileSync(path.join(root, "deployment.json"), "utf8"));
const abi = JSON.parse(fs.readFileSync(path.join(root, "build", "MedicalResearchRegistry.abi.json"), "utf8"));
const provider = new ethers.JsonRpcProvider(deployment.rpcUrl);
const hospitalKeyFile = (process.env.MEDICAL_HOSPITAL_PRIVATE_KEY_FILE || "").trim();
const hospital = hospitalKeyFile
  ? new ethers.Wallet(fs.readFileSync(hospitalKeyFile, "utf8").trim(), provider)
  : await provider.getSigner(0);
const researcher = ethers.Wallet.createRandom();
const hospitalContract = new ethers.Contract(deployment.contractAddress, abi, hospital);

async function sendTx(contract, method, ...args) {
  const fn = contract.getFunction(method);
  const estimated = await fn.estimateGas(...args);
  const tx = await fn(...args, { gasLimit: estimated * 2n });
  return tx.wait();
}

const suffix = Date.now().toString(36);
const datasetId = "ds-" + randomBytes(16).toString("hex");
const requestId = "req-" + randomBytes(16).toString("hex");
const jobId = "JOB-E2E-" + suffix;
const fakeSha = "a".repeat(64);
const fakeResultSha = "b".repeat(64);
const metadataCommitment = "sha256:" + "c".repeat(64);
const purposeCommitment = "sha256:" + "d".repeat(64);
const commitment = ethers.keccak256(ethers.toUtf8Bytes("bafy-demo:" + fakeSha));
const otherResearcher = ethers.Wallet.createRandom();
async function rejects(method, ...args) {
  try {
    await sendTx(hospitalContract, method, ...args);
  } catch (_) {
    return;
  }
  assert.fail(`${method} unexpectedly accepted invalid input`);
}

const hBalance = await provider.getBalance(await hospital.getAddress());
assert(hBalance > ethers.parseEther("90"));

let semanticDatasetBlocked = false;
try {
  await sendTx(
    hospitalContract,
    "registerDataset",
    "patient-alice-diabetes",
    "CSV",
    metadataCommitment,
    "ACTIVE"
  );
} catch (_) {
  semanticDatasetBlocked = true;
}
assert.equal(semanticDatasetBlocked, true);

for (const label of ["hiv_status", "BRCA1_mutation", "cancer_stage", "patient-alice"]) {
  await rejects("registerDataset", "ds-" + randomBytes(16).toString("hex"), label,
    metadataCommitment, "ACTIVE");
}

await sendTx(hospitalContract, "registerDataset", datasetId, "CSV", metadataCommitment, "ACTIVE");
let ds = await hospitalContract.getDataset(datasetId);
assert.equal(ds.storageState, "PRIVATE_PENDING");

await sendTx(hospitalContract, "finalizeDataset", datasetId, commitment);
ds = await hospitalContract.getDataset(datasetId);
assert.equal(ds.storageState, "PRIVATE_READY");

let unauthorized = false;
try {
  const unlockedResearcher = await provider.getSigner(1);
  const unauthorizedContract = hospitalContract.connect(unlockedResearcher);
  await sendTx(unauthorizedContract, "updateConsent", datasetId, "REVOKED");
} catch (_) {
  unauthorized = true;
}
assert.equal(unauthorized, true);

let semanticRequestBlocked = false;
try {
  const invalidRequestId = "req-alice-diabetes-study";
  const invalidDigest = await hospitalContract.researcherRequestDigest(
    invalidRequestId,
    datasetId,
    purposeCommitment
  );
  const invalidSignature = await researcher.signMessage(ethers.getBytes(invalidDigest));
  await sendTx(
    hospitalContract,
    "requestAccessBySig",
    invalidRequestId,
    datasetId,
    purposeCommitment,
    researcher.address,
    invalidSignature
  );
} catch (_) {
  semanticRequestBlocked = true;
}
assert.equal(semanticRequestBlocked, true);

const requestDigest = await hospitalContract.researcherRequestDigest(
  requestId,
  datasetId,
  purposeCommitment
);
const requestSignature = await researcher.signMessage(ethers.getBytes(requestDigest));
const secondFactory = new ethers.ContractFactory(
  abi,
  fs.readFileSync(path.join(root, "build", "MedicalResearchRegistry.bytecode.txt"), "utf8").trim(),
  hospital
);
const secondContract = await secondFactory.deploy();
await secondContract.waitForDeployment();
await sendTx(secondContract, "registerDataset", datasetId, "CSV", metadataCommitment, "ACTIVE");
await sendTx(secondContract, "finalizeDataset", datasetId, commitment);
assert.notEqual(await secondContract.researcherRequestDigest(requestId, datasetId, purposeCommitment), requestDigest);
let crossContractReplayBlocked = false;
try {
  await sendTx(secondContract, "requestAccessBySig", requestId, datasetId,
    purposeCommitment, researcher.address, requestSignature);
} catch (_) {
  crossContractReplayBlocked = true;
}
assert(crossContractReplayBlocked);
await rejects("requestAccessBySig", requestId, datasetId, purposeCommitment,
  researcher.address,
  await otherResearcher.signMessage(ethers.getBytes(requestDigest)));
// The first signature is valid for its own wallet; a request cannot be replayed
// under a different opaque request identifier.
await rejects("requestAccessBySig", "req-" + randomBytes(16).toString("hex"),
  datasetId, purposeCommitment, researcher.address, requestSignature);
await sendTx(
  hospitalContract,
  "requestAccessBySig",
  requestId,
  datasetId,
  purposeCommitment,
  researcher.address,
  requestSignature
);
let req = await hospitalContract.getAccessRequest(requestId);
assert.equal(req.status, "PENDING");
assert.equal(req.requester.toLowerCase(), researcher.address.toLowerCase());

await sendTx(hospitalContract, "decideAccess", requestId, "APPROVED");
assert.equal(await hospitalContract.canAccess(requestId), true);

await sendTx(hospitalContract, "recordKeyRotation", datasetId, 1, 2);
const rotation = await hospitalContract.getKeyRotation(datasetId, 2);
assert.equal(rotation.status, "COMMITTED");

for (const label of ["hiv_status", "BRCA1_mutation", "cancer_stage", "patient-alice"]) {
  await rejects("registerHEJob", jobId + "-" + label, datasetId, requestId,
    "", "", label, 4, "bafy-ciphertext", fakeSha);
}

await sendTx(
  hospitalContract,
  "registerHEJob",
  jobId,
  datasetId,
  requestId,
  "",
  "",
  "CSV:sha256:" + "e".repeat(64),
  4,
  "bafy-ciphertext",
  fakeSha
);

let job = await hospitalContract.getHEJob(jobId);
assert.equal(job.status, "ENCRYPTED");
assert.equal(job.researcher.toLowerCase(), researcher.address.toLowerCase());
assert.equal(job.metric, "CSV:sha256:" + "e".repeat(64));

const computeDigest = await hospitalContract.heComputeDigest(jobId);
const computeSignature = await researcher.signMessage(ethers.getBytes(computeDigest));
await rejects("recordHEComputationBySig", jobId, "bafy-result", fakeResultSha,
  requestSignature);
await rejects("recordHEComputationBySig", jobId, "bafy-result", fakeResultSha,
  await otherResearcher.signMessage(ethers.getBytes(computeDigest)));
const order = BigInt("0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141");
const rawSig = ethers.getBytes(computeSignature);
const highS = ethers.concat([
  rawSig.slice(0, 32),
  ethers.zeroPadValue(ethers.toBeHex(order - BigInt(ethers.hexlify(rawSig.slice(32, 64)))), 32),
  Uint8Array.from([rawSig[64] === 27 ? 28 : 27])
]);
await rejects("recordHEComputationBySig", jobId, "bafy-result", fakeResultSha, highS);
await sendTx(
  hospitalContract,
  "recordHEComputationBySig",
  jobId,
  "bafy-result",
  fakeResultSha,
  computeSignature
);
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
  "CSV:sha256:" + "e".repeat(64),
  4,
  "bafy-ciphertext-revoke",
  fakeSha
);
const revokedComputeDigest = await hospitalContract.heComputeDigest(revokedJobId);
const revokedComputeSignature = await researcher.signMessage(
  ethers.getBytes(revokedComputeDigest)
);
await rejects("recordHEComputationBySig", revokedJobId, "bafy-result-revoke",
  fakeResultSha, computeSignature);
await sendTx(
  hospitalContract,
  "recordHEComputationBySig",
  revokedJobId,
  "bafy-result-revoke",
  fakeResultSha,
  revokedComputeSignature
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

console.log("EXTERNAL RESEARCHER WALLET SIGNATURES: PASS");
console.log("SOLIDITY GOVERNANCE E2E: PASS");
