#include <seal/seal.h>
#include <algorithm>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <vector>

using namespace std;
using namespace seal;

int main()
{
    EncryptionParameters parms;
    {
        ifstream in("research_exchange/parms.seal", ios::binary);
        if (!in) {
            cerr << "Missing CKKS parameters\n";
            return 1;
        }
        parms.load(in);
    }

    SEALContext context(parms);
    if (!context.parameters_set()) {
        cerr << "Invalid CKKS parameters\n";
        return 1;
    }

    SecretKey secret_key;
    {
        ifstream in("hospital_private/secret.key", ios::binary);
        if (!in) {
            cerr << "Hospital secret key unavailable\n";
            return 1;
        }
        secret_key.load(context, in);
    }

    Ciphertext result;
    Ciphertext auxiliary;
    bool has_auxiliary = false;
    {
        ifstream in("research_exchange/result.ct", ios::binary);
        if (!in) {
            cerr << "Encrypted result is missing\n";
            return 1;
        }
        result.load(context, in);
        if (in.peek() != char_traits<char>::eof()) {
            auxiliary.load(context, in);
            has_auxiliary = true;
        }
    }

    Decryptor decryptor(context, secret_key);
    CKKSEncoder encoder(context);

    Plaintext plain;
    decryptor.decrypt(result, plain);

    vector<double> decoded;
    encoder.decode(plain, decoded);

    if (decoded.empty()) {
        cerr << "Decoded result is empty\n";
        return 1;
    }

    cout << setprecision(17);
    cout << "Decrypted result: " << decoded[0] << "\n";
    if (has_auxiliary) {
        Plaintext auxiliary_plain;
        vector<double> auxiliary_decoded;
        decryptor.decrypt(auxiliary, auxiliary_plain);
        encoder.decode(auxiliary_plain, auxiliary_decoded);
        if (auxiliary_decoded.empty()) {
            cerr << "Decoded auxiliary result is empty\n";
            return 1;
        }
        cout << "Decrypted auxiliary result: "
             << auxiliary_decoded[0] << "\n";
    }
    cout << "DICOM HE hospital decryption: PASS\n";
    return 0;
}

