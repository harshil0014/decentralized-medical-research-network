#include <seal/seal.h>
#include <cmath>
#include <fstream>
#include <iostream>
#include <string>

using namespace std;
using namespace seal;

static void reduce_slots(
    Ciphertext &value,
    Evaluator &evaluator,
    const GaloisKeys &galois_keys,
    size_t slot_count)
{
    for (size_t step = 1; step < slot_count; step <<= 1) {
        Ciphertext rotated;
        evaluator.rotate_vector(
            value,
            static_cast<int>(step),
            galois_keys,
            rotated
        );
        evaluator.add_inplace(value, rotated);
    }
}

static void multiply_scalar(
    Ciphertext &value,
    double scalar,
    CKKSEncoder &encoder,
    Evaluator &evaluator,
    double target_scale)
{
    Plaintext plain;
    encoder.encode(
        scalar,
        value.parms_id(),
        target_scale,
        plain
    );
    evaluator.multiply_plain_inplace(value, plain);
    evaluator.rescale_to_next_inplace(value);
    value.scale() = target_scale;
}

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

    GaloisKeys galois_keys;
    RelinKeys relin_keys;
    {
        ifstream in("research_exchange/galois.keys", ios::binary);
        if (!in) {
            cerr << "Missing Galois keys\n";
            return 1;
        }
        galois_keys.load(context, in);
    }
    {
        ifstream in("research_exchange/relin.keys", ios::binary);
        if (!in) {
            cerr << "Missing relinearization keys\n";
            return 1;
        }
        relin_keys.load(context, in);
    }

    Ciphertext total_sum, total_sumsq;
    {
        ifstream in("research_exchange/block_sum.ct", ios::binary);
        if (!in) {
            cerr << "Missing encrypted block sums\n";
            return 1;
        }
        total_sum.load(context, in);
    }
    {
        ifstream in("research_exchange/block_sumsq.ct", ios::binary);
        if (!in) {
            cerr << "Missing encrypted squared sums\n";
            return 1;
        }
        total_sumsq.load(context, in);
    }

    string analysis;
    {
        ifstream in("research_exchange/analysis.txt");
        getline(in, analysis);
    }

    size_t voxel_count = 0;
    {
        ifstream in("research_exchange/voxel_count.txt");
        in >> voxel_count;
    }

    if (voxel_count == 0) {
        cerr << "Invalid voxel count\n";
        return 1;
    }

    Evaluator evaluator(context);
    CKKSEncoder encoder(context);
    const double scale = pow(2.0, 40);

    reduce_slots(
        total_sum,
        evaluator,
        galois_keys,
        encoder.slot_count()
    );
    reduce_slots(
        total_sumsq,
        evaluator,
        galois_keys,
        encoder.slot_count()
    );

    Ciphertext result;

    if (analysis == "SUM") {
        result = total_sum;
    } else if (analysis == "ENERGY") {
        result = total_sumsq;
    } else if (analysis == "MEAN") {
        result = total_sum;
        multiply_scalar(
            result,
            1.0 / static_cast<double>(voxel_count),
            encoder,
            evaluator,
            scale
        );
    } else if (analysis == "SECOND_MOMENT") {
        result = total_sumsq;
        multiply_scalar(
            result,
            1.0 / static_cast<double>(voxel_count),
            encoder,
            evaluator,
            scale
        );
    } else if (analysis == "VARIANCE") {
        Ciphertext mean = total_sum;
        Ciphertext second = total_sumsq;

        const double reciprocal =
            1.0 / static_cast<double>(voxel_count);

        multiply_scalar(
            mean,
            reciprocal,
            encoder,
            evaluator,
            scale
        );
        multiply_scalar(
            second,
            reciprocal,
            encoder,
            evaluator,
            scale
        );

        Ciphertext mean_square = mean;
        evaluator.square_inplace(mean_square);
        evaluator.relinearize_inplace(
            mean_square,
            relin_keys
        );
        evaluator.rescale_to_next_inplace(mean_square);
        mean_square.scale() = scale;

        evaluator.mod_switch_to_inplace(
            second,
            mean_square.parms_id()
        );
        second.scale() = scale;

        evaluator.sub(
            second,
            mean_square,
            result
        );
    } else {
        cerr << "Unsupported DICOM HE analysis\n";
        return 1;
    }

    {
        ofstream out("research_exchange/result.ct", ios::binary);
        result.save(out);
    }

    cout << "DICOM HE researcher computation: PASS\n";
    cout << "Analysis: " << analysis << "\n";
    cout << "Result remains ciphertext\n";
    cout << "No secret key loaded\n";
    return 0;
}

