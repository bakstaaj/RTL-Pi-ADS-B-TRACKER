#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <math.h>
#include <rtl-sdr.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static volatile sig_atomic_t keep_running = 1;

static void handle_signal(int signal_number)
{
    (void)signal_number;
    keep_running = 0;
}

static void print_usage(const char *program)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "\n"
        "Options:\n"
        "  --serial <serial>          RTL-SDR serial number; default 00000162\n"
        "  --freq-hz <hz>             Center frequency; default 162500000\n"
        "  --sample-rate <hz>         Sample rate; default 1024000\n"
        "  --seconds <n>              Capture duration; default 10\n"
        "  --gain-db <db>             Manual tuner gain, for example 40.2\n"
        "  --iq-output <path>         Optional raw unsigned 8-bit I/Q output file\n"
        "  --help                     Display this help\n",
        program);
}

static int parse_u32(const char *text, uint32_t *value)
{
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

static int parse_positive_int(const char *text, int *value)
{
    char *end = NULL;
    long parsed;

    errno = 0;
    parsed = strtol(text, &end, 10);

    if (errno != 0 || end == text || *end != '\0' || parsed <= 0 || parsed > 86400) {
        return -1;
    }

    *value = (int)parsed;
    return 0;
}

static void list_devices(void)
{
    uint32_t count = rtlsdr_get_device_count();

    fprintf(stderr, "Detected RTL-SDR devices: %u\n", count);

    for (uint32_t index = 0; index < count; index++) {
        char manufacturer[256] = {0};
        char product[256] = {0};
        char serial[256] = {0};

        if (rtlsdr_get_device_usb_strings(index, manufacturer, product, serial) == 0) {
            fprintf(stderr, "  %u: %s, %s, SN: %s\n",
                    index, manufacturer, product, serial);
        } else {
            fprintf(stderr, "  %u: %s\n", index, rtlsdr_get_device_name(index));
        }
    }
}

int main(int argc, char **argv)
{
    const char *serial = "00000162";
    const char *iq_output = NULL;
    uint32_t frequency_hz = 162500000U;
    uint32_t sample_rate_hz = 1024000U;
    int seconds = 10;
    int gain_tenth_db = 0;
    int use_manual_gain = 0;

    rtlsdr_dev_t *device = NULL;
    FILE *output = NULL;
    unsigned char *buffer = NULL;
    const int buffer_length = 262144;
    uint64_t target_bytes;
    uint64_t total_bytes = 0;
    double sum_i = 0.0;
    double sum_q = 0.0;
    double sum_power = 0.0;
    double peak_power = 0.0;
    int exit_code = EXIT_FAILURE;

    for (int arg = 1; arg < argc; arg++) {
        if (strcmp(argv[arg], "--serial") == 0 && arg + 1 < argc) {
            serial = argv[++arg];
        } else if (strcmp(argv[arg], "--freq-hz") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &frequency_hz) != 0) {
                fprintf(stderr, "Invalid --freq-hz value.\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[arg], "--sample-rate") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &sample_rate_hz) != 0) {
                fprintf(stderr, "Invalid --sample-rate value.\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[arg], "--seconds") == 0 && arg + 1 < argc) {
            if (parse_positive_int(argv[++arg], &seconds) != 0) {
                fprintf(stderr, "Invalid --seconds value.\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[arg], "--gain-db") == 0 && arg + 1 < argc) {
            char *end = NULL;
            double gain_db;

            errno = 0;
            gain_db = strtod(argv[++arg], &end);

            if (errno != 0 || end == argv[arg] || *end != '\0') {
                fprintf(stderr, "Invalid --gain-db value.\n");
                return EXIT_FAILURE;
            }

            gain_tenth_db = (int)lround(gain_db * 10.0);
            use_manual_gain = 1;
        } else if (strcmp(argv[arg], "--iq-output") == 0 && arg + 1 < argc) {
            iq_output = argv[++arg];
        } else if (strcmp(argv[arg], "--help") == 0) {
            print_usage(argv[0]);
            return EXIT_SUCCESS;
        } else {
            fprintf(stderr, "Unknown or incomplete option: %s\n", argv[arg]);
            print_usage(argv[0]);
            return EXIT_FAILURE;
        }
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    list_devices();

    int device_index = rtlsdr_get_index_by_serial(serial);
    if (device_index < 0) {
        fprintf(stderr, "Unable to find RTL-SDR with serial number %s.\n", serial);
        return EXIT_FAILURE;
    }

    if (rtlsdr_open(&device, (uint32_t)device_index) != 0) {
        fprintf(stderr, "Unable to open RTL-SDR serial %s.\n", serial);
        return EXIT_FAILURE;
    }

    if (rtlsdr_set_sample_rate(device, sample_rate_hz) != 0) {
        fprintf(stderr, "Unable to set sample rate to %u Hz.\n", sample_rate_hz);
        goto cleanup;
    }

    if (rtlsdr_set_center_freq(device, frequency_hz) != 0) {
        fprintf(stderr, "Unable to tune to %u Hz.\n", frequency_hz);
        goto cleanup;
    }

    if (use_manual_gain) {
        if (rtlsdr_set_tuner_gain_mode(device, 1) != 0 ||
            rtlsdr_set_tuner_gain(device, gain_tenth_db) != 0) {
            fprintf(stderr, "Unable to set manual tuner gain.\n");
            goto cleanup;
        }
    } else {
        if (rtlsdr_set_tuner_gain_mode(device, 0) != 0) {
            fprintf(stderr, "Unable to enable automatic tuner gain.\n");
            goto cleanup;
        }
    }

    if (rtlsdr_reset_buffer(device) != 0) {
        fprintf(stderr, "Unable to reset RTL-SDR sample buffer.\n");
        goto cleanup;
    }

    if (iq_output != NULL) {
        output = fopen(iq_output, "wb");
        if (output == NULL) {
            fprintf(stderr, "Unable to create output file %s: %s\n",
                    iq_output, strerror(errno));
            goto cleanup;
        }
    }

    buffer = malloc((size_t)buffer_length);
    if (buffer == NULL) {
        fprintf(stderr, "Unable to allocate sample buffer.\n");
        goto cleanup;
    }

    target_bytes = (uint64_t)sample_rate_hz * (uint64_t)seconds * 2ULL;

    printf("RTL-SDR audio receiver probe\n");
    printf("  Serial:      %s\n", serial);
    printf("  Frequency:   %u Hz\n", rtlsdr_get_center_freq(device));
    printf("  Sample rate: %u Hz\n", rtlsdr_get_sample_rate(device));
    printf("  Gain mode:   %s\n", use_manual_gain ? "manual" : "automatic");
    if (use_manual_gain) {
        printf("  Gain:        %.1f dB\n", rtlsdr_get_tuner_gain(device) / 10.0);
    }
    printf("  Duration:    %d seconds\n", seconds);
    if (iq_output != NULL) {
        printf("  I/Q output:  %s\n", iq_output);
    }
    fflush(stdout);

    while (keep_running && total_bytes < target_bytes) {
        int bytes_read = 0;

        if (rtlsdr_read_sync(device, buffer, buffer_length, &bytes_read) != 0) {
            fprintf(stderr, "RTL-SDR read failed.\n");
            goto cleanup;
        }

        if (bytes_read <= 0) {
            fprintf(stderr, "RTL-SDR returned no sample data.\n");
            goto cleanup;
        }

        if (output != NULL && fwrite(buffer, 1, (size_t)bytes_read, output) != (size_t)bytes_read) {
            fprintf(stderr, "Failed writing I/Q output file.\n");
            goto cleanup;
        }

        for (int index = 0; index + 1 < bytes_read; index += 2) {
            double i_value = ((double)buffer[index] - 127.5) / 127.5;
            double q_value = ((double)buffer[index + 1] - 127.5) / 127.5;
            double power = (i_value * i_value) + (q_value * q_value);

            sum_i += i_value;
            sum_q += q_value;
            sum_power += power;

            if (power > peak_power) {
                peak_power = power;
            }
        }

        total_bytes += (uint64_t)bytes_read;
    }

    {
        double complex_samples = (double)(total_bytes / 2ULL);
        double mean_i = sum_i / complex_samples;
        double mean_q = sum_q / complex_samples;
        double rms = sqrt(sum_power / (2.0 * complex_samples));
        double peak = sqrt(peak_power / 2.0);
        double rms_dbfs = 20.0 * log10(rms > 0.0 ? rms : 1.0e-12);
        double peak_dbfs = 20.0 * log10(peak > 0.0 ? peak : 1.0e-12);
        double captured_seconds = complex_samples / (double)sample_rate_hz;

        printf("\nCapture complete\n");
        printf("  Bytes read:       %llu\n", (unsigned long long)total_bytes);
        printf("  Complex samples:  %.0f\n", complex_samples);
        printf("  Captured seconds: %.3f\n", captured_seconds);
        printf("  Mean I/Q:         %.6f / %.6f\n", mean_i, mean_q);
        printf("  RMS level:        %.2f dBFS\n", rms_dbfs);
        printf("  Peak level:       %.2f dBFS\n", peak_dbfs);
    }

    exit_code = EXIT_SUCCESS;

cleanup:
    if (output != NULL) {
        fclose(output);
    }

    free(buffer);

    if (device != NULL) {
        rtlsdr_close(device);
    }

    return exit_code;
}
