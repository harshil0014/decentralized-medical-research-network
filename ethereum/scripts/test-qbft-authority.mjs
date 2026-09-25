import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { ethers } from "ethers";

const root = fs.mkdtempSync(path.join(os.tmpdir(), "medical-qbft-authority-"));
try {
  const funded = ethers.Wallet.createRandom();
  const wrong = ethers.Wallet.createRandom();
  const genesis = path.join(root, "genesis.json");
  const fundedKey = path.join(root, "funded.key");
  const wrongKey = path.join(root, "wrong.key");
  fs.writeFileSync(genesis, JSON.stringify({ alloc: {
    [funded.address]: { balance: "0x3635c9adc5dea00000" },
  } }));
  fs.writeFileSync(fundedKey, funded.privateKey, { mode: 0o600 });
  fs.writeFileSync(wrongKey, wrong.privateKey, { mode: 0o600 });
  const script = new URL("./check-qbft-authority.mjs", import.meta.url).pathname;
  const good = spawnSync(process.execPath, [script, genesis, fundedKey], { encoding: "utf8" });
  assert.equal(good.status, 0, good.stderr);
  assert.equal(good.stdout.trim(), funded.address);
  const bad = spawnSync(process.execPath, [script, genesis, wrongKey], { encoding: "utf8" });
  assert.notEqual(bad.status, 0);
  assert.match(bad.stderr, /not funded in this persistent QBFT genesis/);
  console.log("PERSISTENT QBFT HOSPITAL AUTHORITY CHECK: PASS");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
