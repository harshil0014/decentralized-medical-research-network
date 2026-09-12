#include <seal/seal.h>

#include <cmath>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace std;
using namespace seal;

int main()
{
    cout << "=== RESEARCHER: HOMOMORPHIC COMPUTATION ===\n\n";

    EncryptionParameters parms;

    {
        ifstream in(
            "research_exchange/parms.seal",
            ios::binary
        );

        if (!in)
        {
            cerr << "Cannot load encryption parameters\n";
            return 1;
        }

        parms.load(in);
    }

    SEALContext context(parms);

    if (!context.parameters_set())
    {
        cerr << "SEAL PARAMETERS: FAIL\n";
        return 1;
    }

    Evaluator evaluator(context);
    CKKSEncoder encoder(context);

    vector<Ciphertext> encrypted_values(3);

    for (size_t i = 0; i < encrypted_values.size(); i++)
    {
        string filename =
            "research_exchange/glucose_" +
            to_string(i + 1) +
            ".ct";

        ifstream in(filename, ios::binary);

        if (!in)
        {
            cerr << "Missing ciphertext: "
                 << filename << "\n";
            return 1;
        }

        encrypted_values[i].load(context, in);
    }

    cout << "Loaded 3 encrypted glucose measurements.\n";
    cout << "No plaintext glucose values loaded.\n";
    cout << "No secret key loaded.\n\n";

    Ciphertext encrypted_sum = encrypted_values[0];

    for (size_t i = 1; i < encrypted_values.size(); i++)
    {
        evaluator.add_inplace(
            encrypted_sum,
            encrypted_values[i]
        );
    }

    double scale = pow(2.0, 40);

    Plaintext divisor;

    encoder.encode(
        1.0 / 3.0,
        encrypted_sum.parms_id(),
        scale,
        divisor
    );

    evaluator.multiply_plain_inplace(
        encrypted_sum,
        divisor
    );

    evaluator.rescale_to_next_inplace(
        encrypted_sum
    );

    {
        ofstream out(
            "research_exchange/glucose_average.ct",
            ios::binary
        );

        encrypted_sum.save(out);
    }

    cout << "Encrypted average computed.\n";
    cout << "Result remains ciphertext.\n";

    cout << "\nRESEARCHER HE COMPUTATION: PASS\n";

    return 0;
}
