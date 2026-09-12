#include <seal/seal.h>

#include <cmath>
#include <iomanip>
#include <iostream>
#include <vector>

using namespace std;
using namespace seal;

int main()
{
    cout << "=== MEDICAL HOMOMORPHIC ENCRYPTION DEMO ===\n\n";

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

    cout << "SEAL PARAMETERS: PASS\n";
    cout << "Scheme: CKKS\n";
    cout << "Polynomial modulus degree: "
         << poly_modulus_degree << "\n\n";

    KeyGenerator keygen(context);

    SecretKey secret_key = keygen.secret_key();

    PublicKey public_key;
    keygen.create_public_key(public_key);

    Encryptor encryptor(context, public_key);
    Evaluator evaluator(context);
    Decryptor decryptor(context, secret_key);
    CKKSEncoder encoder(context);

    double scale = pow(2.0, 40);

    // Synthetic fasting glucose measurements.
    vector<double> glucose = {
        92.5,
        105.2,
        110.3
    };

    vector<Ciphertext> encrypted_values;

    cout << "Hospital side: encrypting "
         << glucose.size()
         << " glucose measurements...\n";

    for (double value : glucose)
    {
        Plaintext plain;
        encoder.encode(value, scale, plain);

        Ciphertext encrypted;
        encryptor.encrypt(plain, encrypted);

        encrypted_values.push_back(std::move(encrypted));
    }

    cout << "ENCRYPTION: PASS\n\n";

    /*
       From this point until decryption, the evaluator operates
       only on ciphertext objects.
    */

    cout << "Research computation side:\n";
    cout << "Computing cohort average using ciphertext only...\n";

    Ciphertext encrypted_sum = encrypted_values[0];

    for (size_t i = 1; i < encrypted_values.size(); i++)
    {
        evaluator.add_inplace(
            encrypted_sum,
            encrypted_values[i]
        );
    }

    Plaintext encrypted_divisor;

    encoder.encode(
        1.0 / static_cast<double>(glucose.size()),
        encrypted_sum.parms_id(),
        scale,
        encrypted_divisor
    );

    evaluator.multiply_plain_inplace(
        encrypted_sum,
        encrypted_divisor
    );

    evaluator.rescale_to_next_inplace(encrypted_sum);

    cout << "HOMOMORPHIC COMPUTATION: PASS\n\n";

    cout << "Hospital / authorized decryptor side:\n";

    Plaintext result_plain;

    decryptor.decrypt(
        encrypted_sum,
        result_plain
    );

    vector<double> result;

    encoder.decode(
        result_plain,
        result
    );

    double expected =
        (glucose[0] + glucose[1] + glucose[2])
        / 3.0;

    cout << fixed << setprecision(4);

    cout << "Expected plaintext average: "
         << expected << " mg/dL\n";

    cout << "Decrypted HE average:       "
         << result[0] << " mg/dL\n";

    double error = abs(result[0] - expected);

    cout << "Absolute CKKS error:        "
         << error << "\n\n";

    if (error < 0.01)
    {
        cout << "MEDICAL HE DEMO: PASS\n";
        return 0;
    }

    cout << "MEDICAL HE DEMO: FAIL\n";
    return 1;
}
