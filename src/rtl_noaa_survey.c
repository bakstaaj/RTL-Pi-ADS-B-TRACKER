#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <math.h>
#include <rtl-sdr.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AUDIO_RATE_HZ       24000U
#define DECIMATION_FACTOR   42U
#define INPUT_RATE_HZ       (AUDIO_RATE_HZ * DECIMATION_FACTOR)
#define TUNING_OFFSET_HZ    (INPUT_RATE_HZ / 4U)
#define INPUT_BUFFER_BYTES  262144
#define WARMUP_READS        3

typedef struct {
    uint32_t frequency_hz;
    double channel_power_dbfs;
    double noise_power_dbfs;
    double snr_db;
} survey_result_t;

static const uint32_t noaa_frequencies_hz[] = {
    162400000U, 162425000U, 162450000U, 162475000U,
    162500000U, 162525000U, 162550000U
};

static void usage(const char *program)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  --serial <serial>       RTL-SDR serial; default 00000162\n"
        "  --seconds <n>           Seconds per NOAA channel; default 2\n"
        "  --gain-db <db>          Manual tuner gain; default 40.2\n"
        "  --json-output <path>    Optional JSON output path\n"
        "  --help                  Display help\n", program);
}

static int parse_positive_int(const char *text, int *value)
{
    char *end = NULL;
    long parsed;
    errno = 0;
    parsed = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed <= 0 || parsed > 30) return -1;
    *value = (int)parsed;
    return 0;
}

static int compare_results(const void *left, const void *right)
{
    const survey_result_t *a = (const survey_result_t *)left;
    const survey_result_t *b = (const survey_result_t *)right;
    if (a->snr_db < b->snr_db) return 1;
    if (a->snr_db > b->snr_db) return -1;
    return 0;
}

static int capture_channel(rtlsdr_dev_t *device, uint32_t frequency_hz, int seconds, survey_result_t *result)
{
    unsigned char *buffer = malloc(INPUT_BUFFER_BYTES);
    uint64_t target_samples = (uint64_t)INPUT_RATE_HZ * (uint64_t)seconds;
    uint64_t total_samples = 0, filtered_samples = 0;
    double raw_i_sum = 0.0, raw_q_sum = 0.0, raw_power_sum = 0.0;
    double fil_i_sum = 0.0, fil_q_sum = 0.0, fil_power_sum = 0.0;
    double block_i = 0.0, block_q = 0.0;
    unsigned int block_count = 0, phase = 0;

    if (!buffer) {
        fprintf(stderr, "Unable to allocate survey buffer.\n");
        return -1;
    }
    if (rtlsdr_set_center_freq(device, frequency_hz + TUNING_OFFSET_HZ) != 0 ||
        rtlsdr_reset_buffer(device) != 0) {
        fprintf(stderr, "Unable to tune/reset at %u Hz.\n", frequency_hz);
        free(buffer);
        return -1;
    }

    for (int warmup = 0; warmup < WARMUP_READS; warmup++) {
        int bytes_read = 0;
        if (rtlsdr_read_sync(device, buffer, INPUT_BUFFER_BYTES, &bytes_read) != 0) {
            fprintf(stderr, "Warmup read failed at %u Hz.\n", frequency_hz);
            free(buffer);
            return -1;
        }
    }

    while (total_samples < target_samples) {
        int bytes_read = 0;
        uint64_t complex_samples;
        if (rtlsdr_read_sync(device, buffer, INPUT_BUFFER_BYTES, &bytes_read) != 0 || bytes_read <= 0) {
            fprintf(stderr, "Survey read failed at %u Hz.\n", frequency_hz);
            free(buffer);
            return -1;
        }
        complex_samples = (uint64_t)bytes_read / 2U;
        if (complex_samples > target_samples - total_samples) complex_samples = target_samples - total_samples;

        for (uint64_t n = 0; n < complex_samples; n++) {
            double raw_i = ((double)buffer[n * 2U] - 127.5) / 127.5;
            double raw_q = ((double)buffer[n * 2U + 1U] - 127.5) / 127.5;
            double shifted_i, shifted_q;

            raw_i_sum += raw_i; raw_q_sum += raw_q;
            raw_power_sum += raw_i * raw_i + raw_q * raw_q;

            switch (phase & 3U) {
                case 0: shifted_i = raw_i;  shifted_q = raw_q;  break;
                case 1: shifted_i = -raw_q; shifted_q = raw_i;  break;
                case 2: shifted_i = -raw_i; shifted_q = -raw_q; break;
                default: shifted_i = raw_q; shifted_q = -raw_i; break;
            }
            phase++;
            block_i += shifted_i; block_q += shifted_q; block_count++;

            if (block_count == DECIMATION_FACTOR) {
                double fi = block_i / (double)DECIMATION_FACTOR;
                double fq = block_q / (double)DECIMATION_FACTOR;
                fil_i_sum += fi; fil_q_sum += fq; fil_power_sum += fi * fi + fq * fq;
                filtered_samples++;
                block_i = 0.0; block_q = 0.0; block_count = 0;
            }
        }
        total_samples += complex_samples;
    }
    free(buffer);

    if (!filtered_samples) return -1;
    {
        double raw_count = (double)total_samples, fil_count = (double)filtered_samples;
        double raw_mi = raw_i_sum / raw_count, raw_mq = raw_q_sum / raw_count;
        double fil_mi = fil_i_sum / fil_count, fil_mq = fil_q_sum / fil_count;
        double raw_var = raw_power_sum / raw_count - raw_mi * raw_mi - raw_mq * raw_mq;
        double channel_var = fil_power_sum / fil_count - fil_mi * fil_mi - fil_mq * fil_mq;
        double noise_var = raw_var / (double)DECIMATION_FACTOR;
        double excess;

        if (channel_var < 1.0e-15) channel_var = 1.0e-15;
        if (noise_var < 1.0e-15) noise_var = 1.0e-15;
        excess = channel_var - noise_var;

        result->frequency_hz = frequency_hz;
        result->channel_power_dbfs = 10.0 * log10(channel_var / 2.0);
        result->noise_power_dbfs = 10.0 * log10(noise_var / 2.0);
        result->snr_db = excess > noise_var * 1.0e-6 ? 10.0 * log10(excess / noise_var) : -30.0;
    }
    return 0;
}

static int write_json(const char *path, const survey_result_t *ranked, size_t count)
{
    FILE *file = fopen(path, "w");
    if (!file) {
        fprintf(stderr, "Unable to write JSON output %s: %s\n", path, strerror(errno));
        return -1;
    }
    fprintf(file, "{\n  \"measurement\": \"estimated_relative_snr_db\",\n");
    fprintf(file, "  \"best_frequency_hz\": %u,\n  \"channels\": [\n", ranked[0].frequency_hz);
    for (size_t i = 0; i < count; i++) {
        fprintf(file,
            "    {\"rank\": %zu, \"frequency_hz\": %u, \"frequency_mhz\": %.3f, "
            "\"estimated_snr_db\": %.2f, \"channel_power_dbfs\": %.2f, \"noise_power_dbfs\": %.2f}%s\n",
            i + 1, ranked[i].frequency_hz, ranked[i].frequency_hz / 1000000.0,
            ranked[i].snr_db, ranked[i].channel_power_dbfs, ranked[i].noise_power_dbfs,
            i + 1 == count ? "" : ",");
    }
    fprintf(file, "  ]\n}\n");
    fclose(file);
    return 0;
}

int main(int argc, char **argv)
{
    const char *serial = "00000162", *json_output = NULL;
    int seconds = 2;
    float gain_db = 40.2f;
    rtlsdr_dev_t *device = NULL;
    survey_result_t results[7];

    for (int arg = 1; arg < argc; arg++) {
        if (strcmp(argv[arg], "--serial") == 0 && arg + 1 < argc) serial = argv[++arg];
        else if (strcmp(argv[arg], "--seconds") == 0 && arg + 1 < argc) {
            if (parse_positive_int(argv[++arg], &seconds) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--gain-db") == 0 && arg + 1 < argc) {
            char *end = NULL; errno = 0; gain_db = strtof(argv[++arg], &end);
            if (errno != 0 || end == argv[arg] || *end != '\0') return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--json-output") == 0 && arg + 1 < argc) json_output = argv[++arg];
        else if (strcmp(argv[arg], "--help") == 0) { usage(argv[0]); return EXIT_SUCCESS; }
        else { usage(argv[0]); return EXIT_FAILURE; }
    }

    {
        int device_index = rtlsdr_get_index_by_serial(serial);
        int gain_tenth_db = (int)lrintf(gain_db * 10.0f);
        if (device_index < 0 || rtlsdr_open(&device, (uint32_t)device_index) != 0) {
            fprintf(stderr, "Unable to open RTL-SDR serial %s.\n", serial);
            return EXIT_FAILURE;
        }
        if (rtlsdr_set_sample_rate(device, INPUT_RATE_HZ) != 0 ||
            rtlsdr_set_tuner_gain_mode(device, 1) != 0 ||
            rtlsdr_set_tuner_gain(device, gain_tenth_db) != 0) {
            fprintf(stderr, "Unable to configure RTL-SDR survey receiver.\n");
            rtlsdr_close(device); return EXIT_FAILURE;
        }
    }

    printf("NOAA channel survey\n  Receiver serial: %s\n  Seconds/channel: %d\n"
           "  Input sample rate: %u Hz\n  Tuning offset: %u Hz\n  RF gain: %.1f dB\n\n",
           serial, seconds, INPUT_RATE_HZ, TUNING_OFFSET_HZ, gain_db);

    for (size_t i = 0; i < 7; i++) {
        if (capture_channel(device, noaa_frequencies_hz[i], seconds, &results[i]) != 0) {
            rtlsdr_close(device); return EXIT_FAILURE;
        }
        printf("  %.3f MHz: estimated SNR %6.2f dB, channel %7.2f dBFS, noise %7.2f dBFS\n",
               results[i].frequency_hz / 1000000.0, results[i].snr_db,
               results[i].channel_power_dbfs, results[i].noise_power_dbfs);
        fflush(stdout);
    }
    rtlsdr_close(device);
    qsort(results, 7, sizeof(results[0]), compare_results);

    printf("\nRanked NOAA channels\n");
    for (size_t i = 0; i < 7; i++)
        printf("  %zu. %.3f MHz  estimated SNR %6.2f dB\n", i + 1,
               results[i].frequency_hz / 1000000.0, results[i].snr_db);
    printf("\nSelected frequency: %.3f MHz\n", results[0].frequency_hz / 1000000.0);

    if (json_output && write_json(json_output, results, 7) != 0) return EXIT_FAILURE;
    if (json_output) printf("JSON results: %s\n", json_output);
    return EXIT_SUCCESS;
}
