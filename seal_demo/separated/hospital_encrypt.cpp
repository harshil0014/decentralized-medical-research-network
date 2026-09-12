#include <seal/seal.h>

#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace std;
using namespace seal;
namespace fs = std::filesystem;

int main()
{
    cout << "=== HOSPITAL: ENCRYPT MEDICAL DATA ===\n\n";

    fs::create_directories("hospital_private");
    fs::create_directories("research_exchange");

    EncryptionParameters parms(scheme_type::ckks);

    size_t poly_modulus_degree = 8192;
    parms.set_poly_modulus_degree(poly_modulus_degree);

    parms.set_coeff_modulus(
        CoeffModulus::Create(
            poly_modulus_degree,
            {60, 40, 40, 60}
        )
    );

    SEALContext context(parms);

    if (!context.parameters_set())
    {
        cerr << "SEAL PARAMETERS: FAIL\n";
        return 1;
    }

    KeyGenerator keygen(context);

    SecretKey secret_key = keygen.secret_key();

    PublicKey public_key;
    keygen.create_public_key(public_key);

    {
        ofstream out(
            "hospital_private/secret.key",
            ios::binary
        );

        secret_key.save(out);
    }

    {
        ofstream out(
            "research_exchange/parms.seal",
            ios::binary
        );

        parms.save(out);
    }

    Encryptor encryptor(context, public_key);
    CKKSEncoder encoder(context);

    double scale = pow(2.0, 40);

    vector<double> glucose = {
        92.5,
        105.2,
        110.3
    };

    cout << "Plaintext values available ONLY on hospital side:\n";

    for (size_t i = 0; i < glucose.size(); i++)
    {
        cout << "  Patient " << (i + 1)
             << ": " << glucose[i]
             << " mg/dL\n";

        Plaintext plain;
        encoder.encode(glucose[i], scale, plain);

        Ciphertext encrypted;
        encryptor.encrypt(plain, encrypted);

        string filename =
            "research_exchange/glucose_" +
            to_string(i + 1) +
            ".ct";

        ofstream out(filename, ios::binary);
        encrypted.save(out);
    }

    cout << "\nSECRET KEY STORED AT:\n";
    cout << "  hospital_private/secret.key\n";

    cout << "\nCIPHERTEXT EXCHANGE CREATED:\n";
    cout << "  research_exchange/\n";

    cout << "\nHOSPITAL ENCRYPTION: PASS\n";

    return 0;
}
