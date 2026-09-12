#include <seal/seal.h>

#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <vector>

using namespace std;
using namespace seal;

int main()
{
    cout << "=== HOSPITAL: DECRYPT HE RESULT ===\n\n";

    EncryptionParameters parms;

    {
        ifstream in(
            "research_exchange/parms.seal",
            ios::binary
        );

        parms.load(in);
    }

    SEALContext context(parms);

    SecretKey secret_key;

    {
        ifstream in(
            "hospital_private/secret.key",
            ios::binary
        );

        if (!in)
        {
            cerr << "Hospital secret key missing\n";
            return 1;
        }

        secret_key.load(context, in);
    }

    Ciphertext encrypted_result;

    {
        ifstream in(
            "research_exchange/glucose_average.ct",
            ios::binary
        );

        if (!in)
        {
            cerr << "Encrypted result missing\n";
            return 1;
        }

        encrypted_result.load(context, in);
    }

    Decryptor decryptor(
        context,
        secret_key
    );

    CKKSEncoder encoder(context);

    Plaintext plain_result;

    decryptor.decrypt(
        encrypted_result,
        plain_result
    );

    vector<double> decoded;

    encoder.decode(
        plain_result,
        decoded
    );

    vector<double> original_values;

    {
        ifstream input("sample_input/glucose_values.csv");
        double value;

        if (!input)
        {
            cerr << "Cannot open hospital validation input\n";
            return 1;
        }

        while (input >> value)
        {
            original_values.push_back(value);
        }
    }

    if (original_values.empty())
    {
        cerr << "No validation values found\n";
        return 1;
    }

    double total = 0.0;

    for (double value : original_values)
    {
        total += value;
    }

    double expected =
        total / static_cast<double>(original_values.size());

    double result = decoded[0];

    double error =
        abs(result - expected);

    cout << fixed << setprecision(6);

    cout << "Expected average:  "
         << expected
         << " mg/dL\n";

    cout << "Decrypted result:  "
         << result
         << " mg/dL\n";

    cout << "CKKS error:        "
         << error
         << "\n\n";

    if (error < 0.01)
    {
        cout << "HOSPITAL DECRYPTION: PASS\n";
        return 0;
    }

    cout << "HOSPITAL DECRYPTION: FAIL\n";
    return 1;
}
