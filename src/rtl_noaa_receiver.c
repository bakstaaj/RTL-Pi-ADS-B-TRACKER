#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <math.h>
#include <rtl-sdr.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AUDIO_RATE_HZ       24000U
#define DECIMATION_FACTOR   42U
#define INPUT_RATE_HZ       (AUDIO_RATE_HZ * DECIMATION_FACTOR)
#define TUNING_OFFSET_HZ    (INPUT_RATE_HZ / 4U)
#define NFM_DEVIATION_HZ    5000.0f
#define DEFAULT_AUDIO_GAIN  15000.0f
#define PI_F                3.14159265358979323846f
#define INPUT_BUFFER_BYTES  262144

static volatile sig_atomic_t keep_running = 1;

typedef struct {
    FILE *file;
    uint32_t sample_rate;
    uint32_t sample_count;
} wav_writer_t;

static void handle_signal(int signal_number)
{
    (void)signal_number;
    keep_running = 0;
}

static void print_usage(const char *program)
{
    fprintf(stderr,
        "Usage: %s --wav-output <path> [options]\n"
        "\n"
        "Options:\n"
        "  --serial <serial>       RTL-SDR serial number; default 00000162\n"
        "  --freq-hz <hz>          NOAA station frequency; default 162400000\n"
        "  --seconds <n>           Capture duration; default 30\n"
        "  --gain-db <db>          Manual tuner gain; default 40.2\n"
        "  --audio-gain <scale>    PCM output gain; default 15000\n"
        "  --wav-output <path>     Required output WAV filename\n"
        "  --help                  Display this help\n",
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

    if (errno != 0 || end == text || *end != '\0' || parsed <= 0 || parsed > 3600) {
        return -1;
    }

    *value = (int)parsed;
    return 0;
}

static void write_u16_le(FILE *file, uint16_t value)
{
    unsigned char bytes[2];

    bytes[0] = (unsigned char)(value & 0xffU);
    bytes[1] = (unsigned char)((value >> 8U) & 0xffU);

    fwrite(bytes, 1, sizeof(bytes), file);
}

static void write_u32_le(FILE *file, uint32_t value)
{
    unsigned char bytes[4];

    bytes[0] = (unsigned char)(value & 0xffU);
    bytes[1] = (unsigned char)((value >> 8U) & 0xffU);
    bytes[2] = (unsigned char)((value >> 16U) & 0xffU);
    bytes[3] = (unsigned char)((value >> 24U) & 0xffU);

    fwrite(bytes, 1, sizeof(bytes), file);
}

static int wav_open(wav_writer_t *writer, const char *path, uint32_t sample_rate)
{
    memset(writer, 0, sizeof(*writer));

    writer->file = fopen(path, "wb");
    if (writer->file == NULL) {
        fprintf(stderr, "Unable to create WAV file %s: %s\n",
                path, strerror(errno));
        return -1;
    }

    writer->sample_rate = sample_rate;

    fwrite("RIFF", 1, 4, writer->file);
    write_u32_le(writer->file, 0U);
    fwrite("WAVE", 1, 4, writer->file);

    fwrite("fmt ", 1, 4, writer->file);
    write_u32_le(writer->file, 16U);
    write_u16_le(writer->file, 1U);
    write_u16_le(writer->file, 1U);
    write_u32_le(writer->file, sample_rate);
    write_u32_le(writer->file, sample_rate * 2U);
    write_u16_le(writer->file, 2U);
    write_u16_le(writer->file, 16U);

    fwrite("data", 1, 4, writer->file);
    write_u32_le(writer->file, 0U);

    return 0;
}

static int wav_write_sample(wav_writer_t *writer, int16_t sample)
{
    if (fwrite(&sample, sizeof(sample), 1, writer->file) != 1) {
        return -1;
    }

    writer->sample_count++;
    return 0;
}

static int wav_close(wav_writer_t *writer)
{
    uint32_t data_bytes;
    uint32_t riff_bytes;

    if (writer->file == NULL) {
        return 0;
    }

    data_bytes = writer->sample_count * 2U;
    riff_bytes = data_bytes + 36U;

    if (fseek(writer->file, 4L, SEEK_SET) != 0) {
        fclose(writer->file);
        writer->file = NULL;
        return -1;
    }

    write_u32_le(writer->file, riff_bytes);

    if (fseek(writer->file, 40L, SEEK_SET) != 0) {
        fclose(writer->file);
        writer->file = NULL;
        return -1;
    }

    write_u32_le(writer->file, data_bytes);

    fclose(writer->file);
    writer->file = NULL;

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
        }
    }
}

static int16_t float_to_pcm16(float sample, float audio_gain)
{
    float scaled = sample * audio_gain;

    if (scaled > 32767.0f) {
        scaled = 32767.0f;
    } else if (scaled < -32768.0f) {
        scaled = -32768.0f;
    }

    return (int16_t)lrintf(scaled);
}

int main(int argc, char **argv)
{
    const char *serial = "00000162";
    const char *wav_output = NULL;
    uint32_t station_frequency_hz = 162400000U;
    int seconds = 30;
    float gain_db = 40.2f;
    float audio_gain = DEFAULT_AUDIO_GAIN;

    rtlsdr_dev_t *device = NULL;
    unsigned char *buffer = NULL;
    wav_writer_t writer;

    uint64_t target_input_samples;
    uint64_t total_input_samples = 0;
    uint64_t audio_samples = 0;

    float accumulate_i = 0.0f;
    float accumulate_q = 0.0f;
    unsigned int decimation_count = 0;
    unsigned int oscillator_phase = 0;

    float previous_i = 0.0f;
    float previous_q = 0.0f;
    int have_previous_complex = 0;

    float deemphasis_state = 0.0f;
    float highpass_previous_input = 0.0f;
    float highpass_previous_output = 0.0f;

    float rms_sum = 0.0f;
    float peak_audio = 0.0f;

    int exit_code = EXIT_FAILURE;

    for (int arg = 1; arg < argc; arg++) {
        if (strcmp(argv[arg], "--serial") == 0 && arg + 1 < argc) {
            serial = argv[++arg];
        } else if (strcmp(argv[arg], "--freq-hz") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &station_frequency_hz) != 0) {
                fprintf(stderr, "Invalid --freq-hz value.\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[arg], "--seconds") == 0 && arg + 1 < argc) {
            if (parse_positive_int(argv[++arg], &seconds) != 0) {
                fprintf(stderr, "Invalid --seconds value.\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[arg], "--gain-db") == 0 && arg + 1 < argc) {
            char *end = NULL;
            errno = 0;
            gain_db = strtof(argv[++arg], &end);

            if (errno != 0 || end == argv[arg] || *end != '\0') {
                fprintf(stderr, "Invalid --gain-db value.\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[arg], "--audio-gain") == 0 && arg + 1 < argc) {
            char *end = NULL;
            errno = 0;
            audio_gain = strtof(argv[++arg], &end);

            if (errno != 0 || end == argv[arg] || *end != '\0' ||
                audio_gain <= 0.0f || audio_gain > 32767.0f) {
                fprintf(stderr, "Invalid --audio-gain value.\n");
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[arg], "--wav-output") == 0 && arg + 1 < argc) {
            wav_output = argv[++arg];
        } else if (strcmp(argv[arg], "--help") == 0) {
            print_usage(argv[0]);
            return EXIT_SUCCESS;
        } else {
            fprintf(stderr, "Unknown or incomplete option: %s\n", argv[arg]);
            print_usage(argv[0]);
            return EXIT_FAILURE;
        }
    }

    if (wav_output == NULL) {
        fprintf(stderr, "--wav-output is required.\n");
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    list_devices();

    {
        int device_index = rtlsdr_get_index_by_serial(serial);
        uint32_t tuner_frequency_hz = station_frequency_hz + TUNING_OFFSET_HZ;
        int gain_tenth_db = (int)lrintf(gain_db * 10.0f);

        if (device_index < 0) {
            fprintf(stderr, "Unable to find RTL-SDR with serial number %s.\n", serial);
            return EXIT_FAILURE;
        }

        if (rtlsdr_open(&device, (uint32_t)device_index) != 0) {
            fprintf(stderr, "Unable to open RTL-SDR serial %s.\n", serial);
            return EXIT_FAILURE;
        }

        if (rtlsdr_set_sample_rate(device, INPUT_RATE_HZ) != 0) {
            fprintf(stderr, "Unable to set input sample rate.\n");
            goto cleanup;
        }

        if (rtlsdr_set_center_freq(device, tuner_frequency_hz) != 0) {
            fprintf(stderr, "Unable to tune RTL-SDR receiver.\n");
            goto cleanup;
        }

        if (rtlsdr_set_tuner_gain_mode(device, 1) != 0 ||
            rtlsdr_set_tuner_gain(device, gain_tenth_db) != 0) {
            fprintf(stderr, "Unable to configure manual tuner gain.\n");
            goto cleanup;
        }

        if (rtlsdr_reset_buffer(device) != 0) {
            fprintf(stderr, "Unable to reset RTL-SDR buffer.\n");
            goto cleanup;
        }

        printf("Native NOAA NFM receiver\n");
        printf("  Serial:              %s\n", serial);
        printf("  Station frequency:   %u Hz\n", station_frequency_hz);
        printf("  Tuner frequency:     %u Hz\n", rtlsdr_get_center_freq(device));
        printf("  Tuning offset:       %u Hz\n", TUNING_OFFSET_HZ);
        printf("  Input sample rate:   %u Hz\n", rtlsdr_get_sample_rate(device));
        printf("  Audio sample rate:   %u Hz\n", AUDIO_RATE_HZ);
        printf("  Gain:                %.1f dB\n", rtlsdr_get_tuner_gain(device) / 10.0);
        printf("  Audio output gain:   %.1f\n", audio_gain);
        printf("  Duration:            %d seconds\n", seconds);
        printf("  WAV output:          %s\n", wav_output);
        fflush(stdout);
    }

    if (wav_open(&writer, wav_output, AUDIO_RATE_HZ) != 0) {
        goto cleanup;
    }

    buffer = malloc(INPUT_BUFFER_BYTES);
    if (buffer == NULL) {
        fprintf(stderr, "Unable to allocate input sample buffer.\n");
        goto cleanup_wav;
    }

    target_input_samples = (uint64_t)INPUT_RATE_HZ * (uint64_t)seconds;

    while (keep_running && total_input_samples < target_input_samples) {
        int bytes_read = 0;
        uint64_t available_complex_samples;

        if (rtlsdr_read_sync(device, buffer, INPUT_BUFFER_BYTES, &bytes_read) != 0) {
            fprintf(stderr, "RTL-SDR sample read failed.\n");
            goto cleanup_wav;
        }

        if (bytes_read <= 0) {
            fprintf(stderr, "RTL-SDR returned no samples.\n");
            goto cleanup_wav;
        }

        available_complex_samples = (uint64_t)bytes_read / 2U;

        if (available_complex_samples > target_input_samples - total_input_samples) {
            available_complex_samples = target_input_samples - total_input_samples;
        }

        for (uint64_t sample_index = 0; sample_index < available_complex_samples; sample_index++) {
            float raw_i = ((float)buffer[sample_index * 2U] - 127.5f) / 127.5f;
            float raw_q = ((float)buffer[sample_index * 2U + 1U] - 127.5f) / 127.5f;
            float shifted_i;
            float shifted_q;

            /*
             * The tuner is offset by +Fs/4. Rotate the desired station
             * from -Fs/4 back to DC using exp(+j*pi*n/2).
             */
            switch (oscillator_phase & 3U) {
                case 0:
                    shifted_i = raw_i;
                    shifted_q = raw_q;
                    break;
                case 1:
                    shifted_i = -raw_q;
                    shifted_q = raw_i;
                    break;
                case 2:
                    shifted_i = -raw_i;
                    shifted_q = -raw_q;
                    break;
                default:
                    shifted_i = raw_q;
                    shifted_q = -raw_i;
                    break;
            }

            oscillator_phase++;
            accumulate_i += shifted_i;
            accumulate_q += shifted_q;
            decimation_count++;

            if (decimation_count == DECIMATION_FACTOR) {
                float current_i = accumulate_i / (float)DECIMATION_FACTOR;
                float current_q = accumulate_q / (float)DECIMATION_FACTOR;

                if (have_previous_complex) {
                    float discriminator_numerator =
                        current_q * previous_i - current_i * previous_q;
                    float discriminator_denominator =
                        current_i * previous_i + current_q * previous_q;
                    float phase_delta =
                        atan2f(discriminator_numerator, discriminator_denominator);

                    float maximum_expected_phase =
                        2.0f * PI_F * NFM_DEVIATION_HZ / (float)AUDIO_RATE_HZ;
                    float demodulated = phase_delta / maximum_expected_phase;

                    /*
                     * 75 microsecond FM de-emphasis, matching the
                     * conventional rtl_fm -E deemp comparison path.
                     */
                    const float deemphasis_tau = 75.0e-6f;
                    const float deemphasis_alpha =
                        1.0f / (1.0f + deemphasis_tau * (float)AUDIO_RATE_HZ);

                    deemphasis_state +=
                        deemphasis_alpha * (demodulated - deemphasis_state);

                    /*
                     * Remove sub-audible DC / tuning residue from output.
                     */
                    const float highpass_alpha = 0.9922f;
                    float highpass_output =
                        deemphasis_state -
                        highpass_previous_input +
                        highpass_alpha * highpass_previous_output;

                    highpass_previous_input = deemphasis_state;
                    highpass_previous_output = highpass_output;

                    {
                        int16_t pcm_sample = float_to_pcm16(highpass_output, audio_gain);
                        float output_normalized = (float)pcm_sample / 32768.0f;

                        if (fabsf(output_normalized) > peak_audio) {
                            peak_audio = fabsf(output_normalized);
                        }

                        rms_sum += output_normalized * output_normalized;

                        if (wav_write_sample(&writer, pcm_sample) != 0) {
                            fprintf(stderr, "Unable to write WAV sample.\n");
                            goto cleanup_wav;
                        }
                    }

                    audio_samples++;
                }

                previous_i = current_i;
                previous_q = current_q;
                have_previous_complex = 1;

                accumulate_i = 0.0f;
                accumulate_q = 0.0f;
                decimation_count = 0;
            }
        }

        total_input_samples += available_complex_samples;
    }

    if (wav_close(&writer) != 0) {
        fprintf(stderr, "Unable to finalize WAV header.\n");
        goto cleanup;
    }

    {
        float audio_seconds = (float)audio_samples / (float)AUDIO_RATE_HZ;
        float rms_level = audio_samples > 0
            ? sqrtf(rms_sum / (float)audio_samples)
            : 0.0f;
        float rms_dbfs = 20.0f * log10f(rms_level > 1.0e-12f ? rms_level : 1.0e-12f);
        float peak_dbfs = 20.0f * log10f(peak_audio > 1.0e-12f ? peak_audio : 1.0e-12f);

        printf("\nAudio capture complete\n");
        printf("  Input samples:       %llu\n",
               (unsigned long long)total_input_samples);
        printf("  Audio samples:       %llu\n",
               (unsigned long long)audio_samples);
        printf("  Audio duration:      %.3f seconds\n", audio_seconds);
        printf("  RMS audio level:     %.2f dBFS\n", rms_dbfs);
        printf("  Peak audio level:    %.2f dBFS\n", peak_dbfs);
    }

    exit_code = EXIT_SUCCESS;
    goto cleanup;

cleanup_wav:
    wav_close(&writer);

cleanup:
    free(buffer);

    if (device != NULL) {
        rtlsdr_close(device);
    }

    return exit_code;
}
