#include <seal/seal.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <string>
#include <vector>

using namespace std;
using namespace seal;
namespace fs = std::filesystem;

int main()
{
    cout << "=== RESEARCHER: HOMOMORPHIC SUM ===\n\n";

    EncryptionParameters parms;
    {
        ifstream in("research_exchange/parms.seal", ios::binary);
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
    vector<fs::path> ciphertext_files;
    regex value_ciphertext("^glucose_[0-9]+\\.ct$");

    for (const auto &entry : fs::directory_iterator("research_exchange"))
    {
        if (!entry.is_regular_file())
            continue;

        const string name = entry.path().filename().string();
        if (regex_match(name, value_ciphertext))
            ciphertext_files.push_back(entry.path());
    }

    sort(ciphertext_files.begin(), ciphertext_files.end());

    if (ciphertext_files.empty())
    {
        cerr << "No encrypted numeric measurements found\n";
        return 1;
    }

    vector<Ciphertext> encrypted_values;
    for (const auto &path : ciphertext_files)
    {
        ifstream in(path, ios::binary);
        if (!in)
        {
            cerr << "Cannot open ciphertext: " << path << "\n";
            return 1;
        }

        Ciphertext encrypted;
        encrypted.load(context, in);
        encrypted_values.push_back(std::move(encrypted));
    }

    cout << "Loaded " << encrypted_values.size()
         << " encrypted measurements.\n";
    cout << "No plaintext values loaded.\n";
    cout << "No secret key loaded.\n\n";

    Ciphertext encrypted_sum = encrypted_values.front();
    for (size_t i = 1; i < encrypted_values.size(); i++)
        evaluator.add_inplace(encrypted_sum, encrypted_values[i]);

    {
        ofstream out("research_exchange/glucose_sum.ct", ios::binary);
        encrypted_sum.save(out);
    }

    cout << "Encrypted sum computed.\n";
    cout << "Result remains ciphertext.\n\n";
    cout << "RESEARCHER HE SUM: PASS\n";

    return 0;
}
