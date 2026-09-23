#include <seal/seal.h>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace std;
using namespace seal;
namespace fs = std::filesystem;

int main()
{
    fs::create_directories("hospital_private");
    fs::create_directories("research_exchange");

    EncryptionParameters parms(scheme_type::ckks);
    size_t degree = 8192;
    parms.set_poly_modulus_degree(degree);
    parms.set_coeff_modulus(
        CoeffModulus::Create(degree, {60, 40, 40, 60})
    );

    SEALContext context(parms);
    if (!context.parameters_set()) {
        cerr << "SEAL parameters are invalid\n";
        return 1;
    }

    KeyGenerator keygen(context);
    SecretKey secret_key = keygen.secret_key();
    PublicKey public_key;
    RelinKeys relin_keys;
    GaloisKeys galois_keys;
    keygen.create_public_key(public_key);
    keygen.create_relin_keys(relin_keys);
    keygen.create_galois_keys(galois_keys);

    {
        ofstream out("hospital_private/secret.key", ios::binary);
        secret_key.save(out);
    }
    {
        ofstream out("research_exchange/parms.seal", ios::binary);
        parms.save(out);
    }
    {
        ofstream out("research_exchange/relin.keys", ios::binary);
        relin_keys.save(out);
    }
    {
        ofstream out("research_exchange/galois.keys", ios::binary);
        galois_keys.save(out);
    }

    CKKSEncoder encoder(context);
    Encryptor encryptor(context, public_key);
    const size_t slots = encoder.slot_count();

    vector<double> block_sums(slots, 0.0);
    vector<double> block_sumsq(slots, 0.0);

    ifstream in("sample_input/dicom_stats.csv");
    if (!in) {
        cerr << "Cannot open DICOM statistics input\n";
        return 1;
    }

    string line;
    size_t count = 0;
    while (getline(in, line)) {
        if (line.empty()) continue;
        if (count >= slots) {
            cerr << "Too many DICOM statistic blocks for CKKS slots\n";
            return 1;
        }

        stringstream ss(line);
        string a, b;
        if (!getline(ss, a, ',') || !getline(ss, b)) {
            cerr << "Invalid DICOM statistics row\n";
            return 1;
        }

        try {
            block_sums[count] = stod(a);
            block_sumsq[count] = stod(b);
        } catch (...) {
            cerr << "Non-numeric DICOM statistics row\n";
            return 1;
        }
        count++;
    }

    if (count == 0) {
        cerr << "No DICOM statistic blocks supplied\n";
        return 1;
    }

    const double scale = pow(2.0, 40);
    Plaintext plain_sum, plain_sumsq;
    encoder.encode(block_sums, scale, plain_sum);
    encoder.encode(block_sumsq, scale, plain_sumsq);

    Ciphertext encrypted_sum, encrypted_sumsq;
    encryptor.encrypt(plain_sum, encrypted_sum);
    encryptor.encrypt(plain_sumsq, encrypted_sumsq);

    {
        ofstream out("research_exchange/block_sum.ct", ios::binary);
        encrypted_sum.save(out);
    }
    {
        ofstream out("research_exchange/block_sumsq.ct", ios::binary);
        encrypted_sumsq.save(out);
    }

    cout << "DICOM HE encryption: PASS\n";
    cout << "Encrypted blocks: " << count << "\n";
    cout << "Secret key remains hospital-only\n";
    return 0;
}

