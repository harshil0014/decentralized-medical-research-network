#include <seal/seal.h>

#include <fstream>
#include <iomanip>
#include <iostream>
#include <vector>

using namespace std;
using namespace seal;

int main()
{
    cout << "=== HOSPITAL: DECRYPT HE SUM ===\n\n";

    EncryptionParameters parms;
    {
        ifstream in("research_exchange/parms.seal", ios::binary);
        if (!in)
        {
            cerr << "Encryption parameters missing\n";
            return 1;
        }
        parms.load(in);
    }

    SEALContext context(parms);

    SecretKey secret_key;
    {
        ifstream in("hospital_private/secret.key", ios::binary);
        if (!in)
        {
            cerr << "Hospital secret key missing\n";
            return 1;
        }
        secret_key.load(context, in);
    }

    Ciphertext encrypted_result;
    {
        ifstream in("research_exchange/glucose_sum.ct", ios::binary);
        if (!in)
        {
            cerr << "Encrypted sum result missing\n";
            return 1;
        }
        encrypted_result.load(context, in);
    }

    Decryptor decryptor(context, secret_key);
    CKKSEncoder encoder(context);
    Plaintext plain_result;
    decryptor.decrypt(encrypted_result, plain_result);

    vector<double> decoded;
    encoder.decode(plain_result, decoded);

    cout << fixed << setprecision(12);
    cout << "Decrypted result:  " << decoded[0] << "\n";
    cout << "HOSPITAL SUM DECRYPTION: PASS\n";
    return 0;
}
