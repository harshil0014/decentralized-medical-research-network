import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const source = fs.readFileSync(path.resolve("frontend/app.js"), "utf8");
const calls = [];
const account = "0x1111111111111111111111111111111111111111";
const wallet = {
  chain: "0x539",
  address: account,
  async request(request) {
    calls.push(request);
    if (request.method === "eth_requestAccounts") return [this.address];
    if (request.method === "eth_chainId") return this.chain;
    if (request.method === "personal_sign") return "0xsigned";
    throw new Error("Unexpected wallet method");
  },
};
const context = vm.createContext({
  window: { ethereum: wallet },
  document: {
    getElementById: () => ({ addEventListener() {} }),
    querySelectorAll: () => [],
  },
  sessionStorage: { getItem: () => null },
  console,
});
vm.runInContext(source, context, { filename: "frontend/app.js" });
vm.runInContext(`state.ethereumAddress = "${account}"; state.ethereumChainId = 1337;`, context);

const sign = () => vm.runInContext('signResearcherDigest("0x1234")', context);
assert.equal(await sign(), "0xsigned");
assert.equal(calls.at(-1).method, "personal_sign");
assert.deepEqual(Array.from(calls.at(-1).params), ["0x1234", account]);

wallet.chain = "0x1";
await assert.rejects(sign(), /wrong network/);
assert.notEqual(calls.at(-1).method, "personal_sign");
wallet.chain = "0x539";
wallet.address = "0x2222222222222222222222222222222222222222";
await assert.rejects(sign(), /does not match/);
context.window.ethereum = null;
await assert.rejects(sign(), /wallet is required/);
console.log("FRONTEND WALLET ADDRESS/CHAIN/SIGNATURE GUARDS: PASS");
