import fs from "node:fs";
import { ethers } from "ethers";

const [genesisPath, keyPath] = process.argv.slice(2);
if (!genesisPath || !keyPath) {
  throw new Error("Usage: check-qbft-authority.mjs <genesis.json> <hospital-key-file>");
}

const genesis = JSON.parse(fs.readFileSync(genesisPath, "utf8"));
const wallet = new ethers.Wallet(fs.readFileSync(keyPath, "utf8").trim());
const entry = Object.entries(genesis.alloc || {}).find(
  ([address]) => address.toLowerCase() === wallet.address.toLowerCase(),
);
if (!entry || BigInt(entry[1].balance || "0x0") <= 0n) {
  throw new Error(
    "Selected Hospital Ethereum key is not funded in this persistent QBFT genesis; " +
    "use the authority key that created the demo network",
  );
}
console.log(wallet.address);
