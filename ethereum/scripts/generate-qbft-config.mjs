import fs from "node:fs";
import path from "node:path";
import { ethers } from "ethers";

const root = path.resolve(process.argv[2] || ".runtime/qbft");
const authRoot = path.resolve(
  process.env.MEDICAL_REGISTRY_AUTH_DIR || path.join(root, "authority")
);
fs.mkdirSync(root, { recursive: true });
fs.mkdirSync(authRoot, { recursive: true });

const wallet = ethers.Wallet.createRandom();
const keyFile = path.join(authRoot, "hospital_eth.key");
fs.writeFileSync(keyFile, wallet.privateKey + "\n", { mode: 0o600 });
fs.chmodSync(keyFile, 0o600);

const allocation = {};
allocation[wallet.address] = {
  balance: "0x3635c9adc5dea00000"
};

const config = {
  genesis: {
    config: {
      chainId: 1337,
      berlinBlock: 0,
      qbft: {
        blockperiodseconds: 2,
        epochlength: 30000,
        requesttimeoutseconds: 4
      }
    },
    nonce: "0x0",
    timestamp: "0x58ee40ba",
    gasLimit: "0x1fffffffffffff",
    difficulty: "0x1",
    mixHash: "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
    coinbase: "0x0000000000000000000000000000000000000000",
    alloc: allocation
  },
  blockchain: {
    nodes: {
      generate: true,
      count: 4
    }
  }
};

fs.writeFileSync(
  path.join(root, "qbftConfigFile.json"),
  JSON.stringify(config, null, 2)
);
fs.writeFileSync(
  path.join(root, "authority.json"),
  JSON.stringify(
    {
      hospitalAddress: wallet.address,
      hospitalPrivateKeyFile: keyFile
    },
    null,
    2
  ),
  { mode: 0o600 }
);
fs.chmodSync(path.join(root, "authority.json"), 0o600);

console.log(JSON.stringify({
  hospitalAddress: wallet.address,
  hospitalPrivateKeyFile: keyFile,
  configFile: path.join(root, "qbftConfigFile.json")
}, null, 2));
