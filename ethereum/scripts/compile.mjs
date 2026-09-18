import fs from "node:fs";
import path from "node:path";
import solc from "solc";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const sourcePath = path.join(root, "contracts", "MedicalResearchRegistry.sol");
const buildDir = path.join(root, "build");
const source = fs.readFileSync(sourcePath, "utf8");

const input = {
  language: "Solidity",
  sources: {
    "MedicalResearchRegistry.sol": { content: source }
  },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    viaIR: true,
    outputSelection: {
      "*": {
        "*": ["abi", "evm.bytecode.object"]
      }
    }
  }
};

const output = JSON.parse(solc.compile(JSON.stringify(input)));
const errors = output.errors || [];
for (const item of errors) {
  const line = item.formattedMessage || item.message;
  if (item.severity === "error") console.error(line);
  else console.warn(line);
}
if (errors.some((item) => item.severity === "error")) {
  process.exit(1);
}

const artifact = output.contracts["MedicalResearchRegistry.sol"]["MedicalResearchRegistry"];
fs.mkdirSync(buildDir, { recursive: true });
fs.writeFileSync(
  path.join(buildDir, "MedicalResearchRegistry.abi.json"),
  JSON.stringify(artifact.abi, null, 2)
);
fs.writeFileSync(
  path.join(buildDir, "MedicalResearchRegistry.bytecode.txt"),
  "0x" + artifact.evm.bytecode.object
);
console.log("SOLIDITY COMPILE: PASS");
