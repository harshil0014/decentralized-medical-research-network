#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "===== 1/7 ETHEREUM + GANACHE ====="
./scripts/setup_ethereum_local.sh

echo
echo "===== 2/7 PYTHON ENV ====="
if [ ! -x backend/.venv/bin/python ]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -r requirements.txt

echo
echo "===== 3/7 PYTHON ETHEREUM ADAPTER ====="
backend/.venv/bin/python scripts/test_ethereum_adapter.py

echo
echo "===== 4/7 IPFS ====="
if docker ps -a --format '{{.Names}}' | grep -qx medical-ipfs; then
  docker start medical-ipfs >/dev/null || true
else
  docker volume create medical-ipfs-data >/dev/null
  docker run -d \
    --name medical-ipfs \
    -v medical-ipfs-data:/data/ipfs \
    -p 127.0.0.1:5001:5001 \
    ipfs/kubo:latest >/dev/null
fi

for i in $(seq 1 60); do
  if docker exec medical-ipfs ipfs id >/dev/null 2>&1; then
    echo "IPFS: PASS"
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "IPFS failed to become ready"
    docker logs medical-ipfs || true
    exit 1
  fi
  sleep 1
done

echo
echo "===== 5/7 MICROSOFT SEAL 4.4 ====="
if ! find /usr/local -name SEALConfig.cmake -print -quit 2>/dev/null | grep -q .; then
  rm -rf /tmp/SEAL
  git clone --depth 1 --branch v4.4.0 https://github.com/microsoft/SEAL.git /tmp/SEAL
  cmake -S /tmp/SEAL -B /tmp/SEAL/build \
    -DSEAL_BUILD_EXAMPLES=OFF \
    -DSEAL_BUILD_TESTS=OFF
  cmake --build /tmp/SEAL/build -j2
  cmake --install /tmp/SEAL/build
fi

rm -rf seal_demo/build
cmake -S seal_demo -B seal_demo/build
cmake --build seal_demo/build -j2

for binary in \
  hospital_encrypt \
  researcher_compute \
  hospital_decrypt \
  researcher_sum \
  hospital_decrypt_sum \
  dicom_hospital_encrypt_stats \
  dicom_researcher_compute_stats \
  dicom_hospital_decrypt_stats \
  dicom_hospital_encrypt_raw \
  dicom_researcher_compute_raw
do
  test -x "seal_demo/build/$binary"
done
echo "SEAL BINARIES: PASS"

echo
echo "===== 6/7 DICOM REGRESSION ====="
backend/.venv/bin/python scripts/test_dicom_series.py
echo "DICOM REGRESSION: PASS"
PYTHONPATH=. backend/.venv/bin/python scripts/test_dicom_he_stats.py
echo "DICOM HE STATISTICS: PASS"

echo
echo "===== 7/7 FULL BACKEND E2E ====="
backend/.venv/bin/python scripts/test_ethereum_backend_e2e.py
PYTHONPATH=. backend/.venv/bin/python scripts/test_dicom_he_backend_e2e.py

echo
echo "========================================="
echo "FULL ETHEREUM MEDICAL STACK: PASS"
echo "========================================="
echo "Ganache + Solidity      PASS"
echo "100 test ETH accounts   PASS"
echo "Python Ethereum adapter PASS"
echo "IPFS                    PASS"
echo "AES encrypted storage   PASS"
echo "Microsoft SEAL HE       PASS"
echo "DICOM regression        PASS"
echo "DICOM HE statistics     PASS"
echo "DICOM HE backend E2E    PASS"
echo "HE Average              PASS"
echo "HE SUM                  PASS"
echo "Approval/revocation     PASS"
