#include <seal/seal.h>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace std;
using namespace seal;
namespace fs = std::filesystem;

static string chunk_name(size_t index)
{
    ostringstream out;
    out << "research_exchange/voxel_chunk_"
        << setw(6) << setfill('0') << index << ".ct";
    return out.str();
}

int main()
{
    fs::create_directories("hospital_private");
    fs::create_directories("research_exchange");

    ifstream raw("sample_input/raw_voxels.f64", ios::binary | ios::ate);
    if (!raw) {
        cerr << "Cannot open raw normalized DICOM voxels\n";
        return 1;
    }

    const streamsize bytes = raw.tellg();
    if (bytes <= 0 || bytes % static_cast<streamsize>(sizeof(double)) != 0) {
        cerr << "Invalid raw voxel input size\n";
        return 1;
    }
    raw.seekg(0, ios::beg);

    const size_t value_count =
        static_cast<size_t>(bytes / static_cast<streamsize>(sizeof(double)));
    EncryptionParameters parms(scheme_type::ckks);
    const size_t degree = 8192;
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
    const double scale = pow(2.0, 40);

    const size_t chunk_count = (value_count + slots - 1) / slots;
    for (size_t chunk = 0; chunk < chunk_count; ++chunk) {
        const size_t begin = chunk * slots;
        const size_t end = min(begin + slots, value_count);

        vector<double> packed(slots, 0.0);
        const streamsize chunk_bytes = static_cast<streamsize>((end - begin) * sizeof(double));
        if (!raw.read(reinterpret_cast<char *>(packed.data()), chunk_bytes)) {
            cerr << "Failed to read raw voxel chunk\n";
            return 1;
        }

        Plaintext plain;
        Ciphertext encrypted;
        encoder.encode(packed, scale, plain);
        encryptor.encrypt(plain, encrypted);

        ofstream out(chunk_name(chunk), ios::binary);
        encrypted.save(out);
    }

    {
        ofstream out("research_exchange/chunk_count.txt");
        out << chunk_count << "\n";
    }

    cout << "DICOM raw-voxel HE encryption: PASS\n";
    cout << "Encrypted voxels: " << value_count << "\n";
    cout << "Ciphertext chunks: " << chunk_count << "\n";
    cout << "Secret key remains hospital-only\n";
    return 0;
}
