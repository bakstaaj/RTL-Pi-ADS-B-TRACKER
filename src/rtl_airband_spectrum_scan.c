#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <rtl-sdr.h>

/* AIRBAND_SPECTRUM_COMPILE_FIX_V1 */
#ifndef AIRBAND_PI
#define AIRBAND_PI 3.14159265358979323846264338327950288
#endif

#define DEFAULT_SERIAL "00000162"
#define DEFAULT_START_HZ 118000000U
#define DEFAULT_END_HZ 137000000U
#define DEFAULT_SAMPLE_RATE_HZ 2048000U
#define DEFAULT_STEP_HZ 25000U
#define DEFAULT_DWELL_MS 80
#define DEFAULT_GAIN_DB 40.2
#define DEFAULT_TOP_N 20
#define MAX_BINS_PER_CHUNK 160
#define READ_BUFFER_BYTES 262144U
#define EDGE_GUARD_HZ 75000U

typedef struct {
    uint32_t frequency_hz;
    uint32_t center_hz;
    int32_t offset_hz;
    double power_db;
    double snr_db;
} candidate_t;

static void usage(const char *program) {
    fprintf(
        stderr,
        "Usage: %s [options]\\n"
        "  --serial <serial>        RTL-SDR serial; default %s\\n"
        "  --start-hz <hz>          Sweep start; default %u\\n"
        "  --end-hz <hz>            Sweep end; default %u\\n"
        "  --sample-rate <hz>       RTL sample rate; default %u\\n"
        "  --step-hz <hz>           Channel grid step; default %u\\n"
        "  --dwell-ms <ms>          Dwell per RF chunk; default %d\\n"
        "  --gain-db <db>           RF gain; default %.1f\\n"
        "  --top-n <count>          Number of candidates; default %d\\n"
        "  --json-output <path>     Also write JSON to file\\n"
        "  --help                   Show this help\\n",
        program,
        DEFAULT_SERIAL,
        DEFAULT_START_HZ,
        DEFAULT_END_HZ,
        DEFAULT_SAMPLE_RATE_HZ,
        DEFAULT_STEP_HZ,
        DEFAULT_DWELL_MS,
        DEFAULT_GAIN_DB,
        DEFAULT_TOP_N
    );
}

static int parse_u32(const char *text, uint32_t *value) {
    char *end = NULL;
    unsigned long parsed;

    errno = 0;
    parsed = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed > UINT32_MAX) {
        return -1;
    }
    *value = (uint32_t)parsed;
    return 0;
}

static int parse_int_range(const char *text, int minimum, int maximum, int *value) {
    char *end = NULL;
    long parsed;

    errno = 0;
    parsed = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed < minimum || parsed > maximum) {
        return -1;
    }
    *value = (int)parsed;
    return 0;
}

static int parse_double_range(const char *text, double minimum, double maximum, double *value) {
    char *end = NULL;
    double parsed;

    errno = 0;
    parsed = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || parsed < minimum || parsed > maximum) {
        return -1;
    }
    *value = parsed;
    return 0;
}

static int candidate_compare_desc(const void *left, const void *right) {
    const candidate_t *a = (const candidate_t *)left;
    const candidate_t *b = (const candidate_t *)right;

    if (a->snr_db < b->snr_db) return 1;
    if (a->snr_db > b->snr_db) return -1;
    if (a->power_db < b->power_db) return 1;
    if (a->power_db > b->power_db) return -1;
    return 0;
}

static int double_compare_asc(const void *left, const void *right) {
    const double a = *(const double *)left;
    const double b = *(const double *)right;

    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
}

static double safe_db10(double value) {
    if (value < 1.0e-24) {
        value = 1.0e-24;
    }
    return 10.0 * log10(value);
}

static int append_candidate(candidate_t *candidates, int *count, int capacity, candidate_t candidate) {
    if (*count >= capacity) {
        return -1;
    }
    candidates[*count] = candidate;
    *count += 1;
    return 0;
}

static void emit_json(
    FILE *out,
    const candidate_t *candidates,
    int count,
    uint32_t start_hz,
    uint32_t end_hz,
    uint32_t sample_rate_hz,
    uint32_t step_hz,
    int dwell_ms,
    double gain_db
) {
    fprintf(out, "{\n");
    fprintf(out, "  \"scanner\": \"rtl_airband_spectrum_scan\",\n");
    fprintf(out, "  \"start_hz\": %u,\n", start_hz);
    fprintf(out, "  \"end_hz\": %u,\n", end_hz);
    fprintf(out, "  \"sample_rate_hz\": %u,\n", sample_rate_hz);
    fprintf(out, "  \"step_hz\": %u,\n", step_hz);
    fprintf(out, "  \"dwell_ms\": %d,\n", dwell_ms);
    fprintf(out, "  \"gain_db\": %.1f,\n", gain_db);
    fprintf(out, "  \"candidate_count\": %d,\n", count);
    fprintf(out, "  \"candidates\": [\n");

    for (int index = 0; index < count; index++) {
        const candidate_t *candidate = &candidates[index];
        fprintf(out, "    {\n");
        fprintf(out, "      \"frequency_hz\": %u,\n", candidate->frequency_hz);
        fprintf(out, "      \"frequency_mhz\": %.6f,\n", candidate->frequency_hz / 1000000.0);
        fprintf(out, "      \"center_hz\": %u,\n", candidate->center_hz);
        fprintf(out, "      \"offset_hz\": %d,\n", candidate->offset_hz);
        fprintf(out, "      \"power_db\": %.2f,\n", candidate->power_db);
        fprintf(out, "      \"estimated_snr_db\": %.2f\n", candidate->snr_db);
        fprintf(out, "    }%s\n", index + 1 == count ? "" : ",");
    }

    fprintf(out, "  ]\n");
    fprintf(out, "}\n");
}
/* AIRBAND_SPECTRUM_EMIT_JSON_FIX_V2 */


int main(int argc, char **argv) {
    const char *serial = DEFAULT_SERIAL;
    const char *json_output_path = NULL;
    uint32_t start_hz = DEFAULT_START_HZ;
    uint32_t end_hz = DEFAULT_END_HZ;
    uint32_t sample_rate_hz = DEFAULT_SAMPLE_RATE_HZ;
    uint32_t step_hz = DEFAULT_STEP_HZ;
    int dwell_ms = DEFAULT_DWELL_MS;
    int top_n = DEFAULT_TOP_N;
    double gain_db = DEFAULT_GAIN_DB;

    rtlsdr_dev_t *device = NULL;
    unsigned char *buffer = NULL;
    float *samples_i = NULL;
    float *samples_q = NULL;
    candidate_t *all_candidates = NULL;
    int all_candidate_count = 0;
    int all_candidate_capacity = 0;
    int exit_code = EXIT_FAILURE;

    for (int arg = 1; arg < argc; arg++) {
        if (strcmp(argv[arg], "--serial") == 0 && arg + 1 < argc) {
            serial = argv[++arg];
        } else if (strcmp(argv[arg], "--start-hz") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &start_hz) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--end-hz") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &end_hz) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--sample-rate") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &sample_rate_hz) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--step-hz") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &step_hz) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--dwell-ms") == 0 && arg + 1 < argc) {
            if (parse_int_range(argv[++arg], 20, 2000, &dwell_ms) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--gain-db") == 0 && arg + 1 < argc) {
            if (parse_double_range(argv[++arg], 0.0, 49.6, &gain_db) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--top-n") == 0 && arg + 1 < argc) {
            if (parse_int_range(argv[++arg], 1, 200, &top_n) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--json-output") == 0 && arg + 1 < argc) {
            json_output_path = argv[++arg];
        } else if (strcmp(argv[arg], "--help") == 0) {
            usage(argv[0]);
            return EXIT_SUCCESS;
        } else {
            usage(argv[0]);
            return EXIT_FAILURE;
        }
    }

    if (start_hz < 100000000U || end_hz <= start_hz || end_hz > 150000000U) {
        fprintf(stderr, "Invalid sweep range.\\n");
        return EXIT_FAILURE;
    }
    if (sample_rate_hz < 1000000U || sample_rate_hz > 3200000U || step_hz < 5000U || step_hz > 100000U) {
        fprintf(stderr, "Invalid sample rate or grid step.\\n");
        return EXIT_FAILURE;
    }

    const uint32_t useful_span_hz = sample_rate_hz > EDGE_GUARD_HZ * 2U ? sample_rate_hz - EDGE_GUARD_HZ * 2U : sample_rate_hz / 2U;
    const uint32_t half_useful_span_hz = useful_span_hz / 2U;
    const uint32_t first_center_hz = start_hz + half_useful_span_hz;
    const uint32_t center_step_hz = useful_span_hz;
    const uint64_t target_samples_u64 = ((uint64_t)sample_rate_hz * (uint64_t)dwell_ms) / 1000ULL;
    if (target_samples_u64 == 0ULL || target_samples_u64 > 4000000ULL) {
        fprintf(stderr, "Invalid target sample count.\\n");
        return EXIT_FAILURE;
    }

    const int target_samples = (int)target_samples_u64;
    buffer = (unsigned char *)malloc(READ_BUFFER_BYTES);
    samples_i = (float *)malloc((size_t)target_samples * sizeof(float));
    samples_q = (float *)malloc((size_t)target_samples * sizeof(float));
    /*
     * AIRBAND_SPECTRUM_CANDIDATE_CAPACITY_FIX_V1
     *
     * Candidate capacity must account for overlapping RF chunks. A simple
     * range/step estimate is not enough because edge guards and overlap can
     * make the same 25 kHz grid channel appear in more than one chunk.
     */
    const uint32_t channel_estimate = ((end_hz - start_hz) / step_hz) + 32U;
    const uint32_t chunk_estimate = ((end_hz - start_hz) / center_step_hz) + 4U;
    all_candidate_capacity = (int)(channel_estimate * chunk_estimate);
    if (all_candidate_capacity < top_n * 4) {
        all_candidate_capacity = top_n * 4;
    }
    all_candidates = (candidate_t *)calloc((size_t)all_candidate_capacity, sizeof(candidate_t));

    if (!buffer || !samples_i || !samples_q || !all_candidates) {
        fprintf(stderr, "Memory allocation failed.\\n");
        goto cleanup;
    }

    int device_index = rtlsdr_get_index_by_serial(serial);
    if (device_index < 0 || rtlsdr_open(&device, (uint32_t)device_index) != 0) {
        fprintf(stderr, "Unable to open RTL-SDR serial %s.\\n", serial);
        goto cleanup;
    }

    if (
        rtlsdr_set_sample_rate(device, sample_rate_hz) != 0 ||
        rtlsdr_set_tuner_gain_mode(device, 1) != 0 ||
        rtlsdr_set_tuner_gain(device, (int)lrint(gain_db * 10.0)) != 0
    ) {
        fprintf(stderr, "Unable to configure RTL-SDR spectrum scanner.\\n");
        goto cleanup;
    }

    for (uint32_t center_hz = first_center_hz; center_hz < end_hz + half_useful_span_hz; center_hz += center_step_hz) {
        if (center_hz > end_hz) {
            center_hz = end_hz - half_useful_span_hz;
        }

        uint32_t chunk_start = center_hz > half_useful_span_hz ? center_hz - half_useful_span_hz : start_hz;
        uint32_t chunk_end = center_hz + half_useful_span_hz;
        if (chunk_start < start_hz) chunk_start = start_hz;
        if (chunk_end > end_hz) chunk_end = end_hz;

        if (rtlsdr_set_center_freq(device, center_hz) != 0 || rtlsdr_reset_buffer(device) != 0) {
            fprintf(stderr, "Unable to tune spectrum scanner to %u Hz.\\n", center_hz);
            goto cleanup;
        }

        int collected = 0;
        while (collected < target_samples) {
            int bytes_read = 0;
            if (rtlsdr_read_sync(device, buffer, READ_BUFFER_BYTES, &bytes_read) != 0 || bytes_read <= 0) {
                fprintf(stderr, "RTL-SDR read failed.\\n");
                goto cleanup;
            }
            int available_samples = bytes_read / 2;
            int needed = target_samples - collected;
            int copy_samples = available_samples < needed ? available_samples : needed;
            for (int n = 0; n < copy_samples; n++) {
                samples_i[collected + n] = ((float)buffer[(size_t)n * 2U] - 127.5f) / 127.5f;
                samples_q[collected + n] = ((float)buffer[(size_t)n * 2U + 1U] - 127.5f) / 127.5f;
            }
            collected += copy_samples;
        }

        uint32_t bin_frequencies[MAX_BINS_PER_CHUNK];
        double bin_powers[MAX_BINS_PER_CHUNK];
        int bin_count = 0;

        uint32_t first_bin = ((chunk_start + step_hz - 1U) / step_hz) * step_hz;
        for (uint32_t frequency_hz = first_bin; frequency_hz <= chunk_end && bin_count < MAX_BINS_PER_CHUNK; frequency_hz += step_hz) {
            int32_t offset_hz = (int32_t)frequency_hz - (int32_t)center_hz;
            if (llabs((long long)offset_hz) > (long long)(sample_rate_hz / 2U - EDGE_GUARD_HZ)) {
                continue;
            }

            const double angle_step = -2.0 * AIRBAND_PI * (double)offset_hz / (double)sample_rate_hz;
            const double rot_i = cos(angle_step);
            const double rot_q = sin(angle_step);
            double osc_i = 1.0;
            double osc_q = 0.0;
            double sum_i = 0.0;
            double sum_q = 0.0;

            for (int n = 0; n < target_samples; n++) {
                const double i = (double)samples_i[n];
                const double q = (double)samples_q[n];
                sum_i += i * osc_i - q * osc_q;
                sum_q += i * osc_q + q * osc_i;

                const double next_i = osc_i * rot_i - osc_q * rot_q;
                const double next_q = osc_i * rot_q + osc_q * rot_i;
                osc_i = next_i;
                osc_q = next_q;
            }

            const double normalized_i = sum_i / (double)target_samples;
            const double normalized_q = sum_q / (double)target_samples;
            const double power = normalized_i * normalized_i + normalized_q * normalized_q;

            bin_frequencies[bin_count] = frequency_hz;
            bin_powers[bin_count] = power;
            bin_count++;
        }

        if (bin_count > 0) {
            double sorted_powers[MAX_BINS_PER_CHUNK];
            for (int index = 0; index < bin_count; index++) {
                sorted_powers[index] = bin_powers[index];
            }
            qsort(sorted_powers, (size_t)bin_count, sizeof(double), double_compare_asc);
            const double noise_floor = sorted_powers[bin_count / 2];
            const double noise_floor_db = safe_db10(noise_floor);

            for (int index = 0; index < bin_count; index++) {
                const double power_db = safe_db10(bin_powers[index]);
                candidate_t candidate;
                candidate.frequency_hz = bin_frequencies[index];
                candidate.center_hz = center_hz;
                candidate.offset_hz = (int32_t)bin_frequencies[index] - (int32_t)center_hz;
                candidate.power_db = power_db;
                candidate.snr_db = power_db - noise_floor_db;
                if (append_candidate(all_candidates, &all_candidate_count, all_candidate_capacity, candidate) != 0) {
                    fprintf(stderr, "Too many candidates.\\n");
                    goto cleanup;
                }
            }
        }

        if (center_hz + half_useful_span_hz >= end_hz) {
            break;
        }
    }

    qsort(all_candidates, (size_t)all_candidate_count, sizeof(candidate_t), candidate_compare_desc);
    if (all_candidate_count > top_n) {
        all_candidate_count = top_n;
    }

    emit_json(stdout, all_candidates, all_candidate_count, start_hz, end_hz, sample_rate_hz, step_hz, dwell_ms, gain_db);

    if (json_output_path) {
        FILE *json_file = fopen(json_output_path, "w");
        if (!json_file) {
            fprintf(stderr, "Unable to open JSON output %s: %s\\n", json_output_path, strerror(errno));
            goto cleanup;
        }
        emit_json(json_file, all_candidates, all_candidate_count, start_hz, end_hz, sample_rate_hz, step_hz, dwell_ms, gain_db);
        fclose(json_file);
    }

    exit_code = EXIT_SUCCESS;

cleanup:
    if (device) {
        rtlsdr_close(device);
    }
    free(buffer);
    free(samples_i);
    free(samples_q);
    free(all_candidates);
    return exit_code;
}
