#include <seal/seal.h>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

using namespace std;
using namespace seal;

static string chunk_name(size_t index)
{
    ostringstream out;
    out << "research_exchange/voxel_chunk_"
        << setw(6) << setfill('0') << index << ".ct";
    return out.str();
}

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

static void add_total(
    Ciphertext &total,
    bool &has_total,
    const Ciphertext &partial,
    Evaluator &evaluator)
{
    if (!has_total) {
        total = partial;
        has_total = true;
    } else {
        evaluator.add_inplace(total, partial);
    }
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

    string analysis;
    {
        ifstream in("research_exchange/analysis.txt");
        getline(in, analysis);
    }

    size_t voxel_count = 0;
    size_t chunk_count = 0;
    {
        ifstream in("research_exchange/voxel_count.txt");
        in >> voxel_count;
    }
    {
        ifstream in("research_exchange/chunk_count.txt");
        in >> chunk_count;
    }

    if (voxel_count == 0 || chunk_count == 0) {
        cerr << "Invalid raw-voxel job metadata\n";
        return 1;
    }

    const bool need_sum =
        analysis == "SUM" ||
        analysis == "MEAN" ||
        analysis == "VARIANCE" ||
        analysis == "STANDARD_DEVIATION";

    const bool need_sumsq =
        analysis == "ENERGY" ||
        analysis == "TOTAL_ENERGY" ||
        analysis == "SECOND_MOMENT" ||
        analysis == "ROOT_MEAN_SQUARED" ||
        analysis == "VARIANCE" ||
        analysis == "STANDARD_DEVIATION" ||
        analysis == "SKEWNESS" ||
        analysis == "KURTOSIS";

    const bool need_sumcube =
        analysis == "SKEWNESS" ||
        analysis == "CENTRAL_MOMENT_3";

    const bool need_sumfourth =
        analysis == "KURTOSIS" ||
        analysis == "CENTRAL_MOMENT_4";

    if (!need_sum && !need_sumsq && !need_sumcube && !need_sumfourth) {
        cerr << "Unsupported DICOM HE analysis\n";
        return 1;
    }

    Evaluator evaluator(context);
    CKKSEncoder encoder(context);
    const double scale = pow(2.0, 40);
    const size_t slots = encoder.slot_count();

    Ciphertext total_sum;
    Ciphertext total_sumsq;
    Ciphertext total_sumcube;
    Ciphertext total_sumfourth;
    bool have_sum = false;
    bool have_sumsq = false;
    bool have_sumcube = false;
    bool have_sumfourth = false;

    for (size_t chunk = 0; chunk < chunk_count; ++chunk) {
        Ciphertext encrypted;
        {
            ifstream in(chunk_name(chunk), ios::binary);
            if (!in) {
                cerr << "Missing encrypted raw voxel chunk\n";
                return 1;
            }
            encrypted.load(context, in);
        }

        if (need_sum) {
            Ciphertext partial = encrypted;
            reduce_slots(partial, evaluator, galois_keys, slots);
            add_total(total_sum, have_sum, partial, evaluator);
        }

        Ciphertext square;
        bool have_square = false;
        if (need_sumsq || need_sumcube || need_sumfourth) {
            square = encrypted;
            evaluator.square_inplace(square);
            evaluator.relinearize_inplace(square, relin_keys);
            evaluator.rescale_to_next_inplace(square);
            square.scale() = scale;
            have_square = true;
        }

        if (need_sumsq && have_square) {
            Ciphertext partial = square;
            reduce_slots(partial, evaluator, galois_keys, slots);
            add_total(total_sumsq, have_sumsq, partial, evaluator);
        }

        if (need_sumcube && have_square) {
            Ciphertext base = encrypted;
            evaluator.mod_switch_to_inplace(base, square.parms_id());
            base.scale() = scale;

            Ciphertext cube;
            evaluator.multiply(square, base, cube);
            evaluator.relinearize_inplace(cube, relin_keys);
            evaluator.rescale_to_next_inplace(cube);
            cube.scale() = scale;

            reduce_slots(cube, evaluator, galois_keys, slots);
            add_total(total_sumcube, have_sumcube, cube, evaluator);
        }

        if (need_sumfourth && have_square) {
            Ciphertext fourth = square;
            evaluator.square_inplace(fourth);
            evaluator.relinearize_inplace(fourth, relin_keys);
            evaluator.rescale_to_next_inplace(fourth);
            fourth.scale() = scale;

            reduce_slots(fourth, evaluator, galois_keys, slots);
            add_total(total_sumfourth, have_sumfourth, fourth, evaluator);
        }
    }

    Ciphertext result;
    Ciphertext auxiliary;
    bool has_auxiliary = false;
    const double reciprocal = 1.0 / static_cast<double>(voxel_count);

    if (analysis == "SUM") {
        result = total_sum;
    } else if (analysis == "MEAN") {
        result = total_sum;
        multiply_scalar(result, reciprocal, encoder, evaluator, scale);
    } else if (analysis == "ENERGY" || analysis == "TOTAL_ENERGY") {
        result = total_sumsq;
    } else if (
        analysis == "SECOND_MOMENT" ||
        analysis == "ROOT_MEAN_SQUARED"
    ) {
        result = total_sumsq;
        multiply_scalar(result, reciprocal, encoder, evaluator, scale);
    } else if (
        analysis == "VARIANCE" ||
        analysis == "STANDARD_DEVIATION"
    ) {
        Ciphertext mean = total_sum;
        Ciphertext second = total_sumsq;

        multiply_scalar(mean, reciprocal, encoder, evaluator, scale);
        multiply_scalar(second, reciprocal, encoder, evaluator, scale);

        Ciphertext mean_square = mean;
        evaluator.square_inplace(mean_square);
        evaluator.relinearize_inplace(mean_square, relin_keys);
        evaluator.rescale_to_next_inplace(mean_square);
        mean_square.scale() = scale;

        evaluator.mod_switch_to_inplace(
            second,
            mean_square.parms_id()
        );
        second.scale() = scale;
        evaluator.sub(second, mean_square, result);
    } else if (analysis == "CENTRAL_MOMENT_3") {
        result = total_sumcube;
    } else if (analysis == "CENTRAL_MOMENT_4") {
        result = total_sumfourth;
    } else if (analysis == "SKEWNESS") {
        // Keep both encrypted moments in the same serialized artifact.
        // Hospital derives the final dimensionless ratio only after decrypting
        // the researcher-produced ciphertexts.
        result = total_sumsq;
        auxiliary = total_sumcube;
        has_auxiliary = true;
    } else if (analysis == "KURTOSIS") {
        result = total_sumsq;
        auxiliary = total_sumfourth;
        has_auxiliary = true;
    }

    {
        ofstream out("research_exchange/result.ct", ios::binary);
        result.save(out);
        if (has_auxiliary) {
            auxiliary.save(out);
        }
    }

    cout << "DICOM raw-voxel researcher computation: PASS\n";
    cout << "Analysis: " << analysis << "\n";
    cout << "Result remains ciphertext\n";
    cout << "No secret key loaded\n";
    return 0;
}
