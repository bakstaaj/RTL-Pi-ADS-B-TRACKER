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
#define DEFAULT_AUDIO_GAIN  120000.0f
#define INPUT_BUFFER_BYTES  262144

static volatile sig_atomic_t keep_running = 1;

typedef struct {
    FILE *file;
    uint32_t sample_count;
} wav_writer_t;

static void handle_signal(int signal_number)
{
    (void)signal_number;
    keep_running = 0;
}

static void usage(const char *program)
{
    fprintf(stderr,
        "Usage: %s --freq-hz <hz> --wav-output <path> [options]\n"
        "  --serial <serial>       RTL-SDR serial; default 00000162\n"
        "  --freq-hz <hz>          Civil-airband AM channel frequency\n"
        "  --seconds <n>           Capture duration in whole seconds; default 20\n"
        "  --duration-ms <n>       Capture duration in milliseconds; overrides --seconds\n"
        "  --gain-db <db>          RF tuner gain; default 40.2\n"
        "  --audio-gain <scale>    PCM output gain; default 120000\n"
        "  --wav-output <path>     Output WAV filename\n"
        "  --help                  Display help\n",
        program);
}

static void write_u16_le(FILE *file, uint16_t value)
{
    unsigned char b[2] = {(unsigned char)(value & 0xffU), (unsigned char)((value >> 8U) & 0xffU)};
    fwrite(b, 1, 2, file);
}

static void write_u32_le(FILE *file, uint32_t value)
{
    unsigned char b[4] = {
        (unsigned char)(value & 0xffU), (unsigned char)((value >> 8U) & 0xffU),
        (unsigned char)((value >> 16U) & 0xffU), (unsigned char)((value >> 24U) & 0xffU)
    };
    fwrite(b, 1, 4, file);
}

static int wav_open(wav_writer_t *writer, const char *path)
{
    memset(writer, 0, sizeof(*writer));
    writer->file = fopen(path, "wb");
    if (!writer->file) {
        fprintf(stderr, "Unable to create WAV file %s: %s\n", path, strerror(errno));
        return -1;
    }
    fwrite("RIFF", 1, 4, writer->file); write_u32_le(writer->file, 0U); fwrite("WAVE", 1, 4, writer->file);
    fwrite("fmt ", 1, 4, writer->file); write_u32_le(writer->file, 16U);
    write_u16_le(writer->file, 1U); write_u16_le(writer->file, 1U);
    write_u32_le(writer->file, AUDIO_RATE_HZ); write_u32_le(writer->file, AUDIO_RATE_HZ * 2U);
    write_u16_le(writer->file, 2U); write_u16_le(writer->file, 16U);
    fwrite("data", 1, 4, writer->file); write_u32_le(writer->file, 0U);
    return 0;
}

static int wav_write(wav_writer_t *writer, int16_t sample)
{
    unsigned char b[2] = {(unsigned char)(sample & 0xff), (unsigned char)(((uint16_t)sample >> 8U) & 0xffU)};
    if (fwrite(b, 1, 2, writer->file) != 2) return -1;
    writer->sample_count++;
    return 0;
}

static void wav_close(wav_writer_t *writer)
{
    if (!writer->file) return;
    uint32_t data_bytes = writer->sample_count * 2U;
    fseek(writer->file, 4L, SEEK_SET); write_u32_le(writer->file, 36U + data_bytes);
    fseek(writer->file, 40L, SEEK_SET); write_u32_le(writer->file, data_bytes);
    fclose(writer->file);
    writer->file = NULL;
}

static int parse_u32(const char *text, uint32_t *value)
{
    char *end = NULL;
    unsigned long parsed;
    errno = 0;
    parsed = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed > UINT32_MAX) return -1;
    *value = (uint32_t)parsed;
    return 0;
}

static int parse_positive_int(const char *text, int maximum, int *value)
{
    char *end = NULL;
    long parsed;
    errno = 0;
    parsed = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed <= 0 || parsed > maximum) return -1;
    *value = (int)parsed;
    return 0;
}

static int16_t to_pcm16(float sample, float gain)
{
    float scaled = sample * gain;
    if (scaled > 32767.0f) scaled = 32767.0f;
    if (scaled < -32768.0f) scaled = -32768.0f;
    return (int16_t)lrintf(scaled);
}

int main(int argc, char **argv)
{
    const char *serial = "00000162";
    const char *wav_path = NULL;
    uint32_t channel_hz = 0U;
    int duration_ms = 20000;
    float gain_db = 40.2f;
    float audio_gain = DEFAULT_AUDIO_GAIN;
    rtlsdr_dev_t *device = NULL;
    unsigned char *buffer = NULL;
    wav_writer_t writer = {0};
    uint64_t target_samples, total_samples = 0, audio_samples = 0;
    float sum_i = 0.0f, sum_q = 0.0f;
    unsigned int decimation_count = 0, oscillator_phase = 0;
    float envelope_dc = 0.0f, lowpass_state = 0.0f;
    float hp_prev_input = 0.0f, hp_prev_output = 0.0f;
    double output_square_sum = 0.0;
    float output_peak = 0.0f;
    double raw_i_sum = 0.0, raw_q_sum = 0.0, raw_power_sum = 0.0;
    double chan_power_sum = 0.0;
    int exit_code = EXIT_FAILURE;

    for (int arg = 1; arg < argc; arg++) {
        if (strcmp(argv[arg], "--serial") == 0 && arg + 1 < argc) serial = argv[++arg];
        else if (strcmp(argv[arg], "--freq-hz") == 0 && arg + 1 < argc) {
            if (parse_u32(argv[++arg], &channel_hz) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--seconds") == 0 && arg + 1 < argc) {
            int seconds;
            if (parse_positive_int(argv[++arg], 3600, &seconds) != 0) return EXIT_FAILURE;
            duration_ms = seconds * 1000;
        } else if (strcmp(argv[arg], "--duration-ms") == 0 && arg + 1 < argc) {
            if (parse_positive_int(argv[++arg], 3600000, &duration_ms) != 0) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--gain-db") == 0 && arg + 1 < argc) {
            char *end = NULL; gain_db = strtof(argv[++arg], &end);
            if (end == argv[arg] || *end != '\0') return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--audio-gain") == 0 && arg + 1 < argc) {
            char *end = NULL; audio_gain = strtof(argv[++arg], &end);
            if (end == argv[arg] || *end != '\0' || audio_gain <= 0.0f || audio_gain > 10000000.0f) return EXIT_FAILURE;
        } else if (strcmp(argv[arg], "--wav-output") == 0 && arg + 1 < argc) wav_path = argv[++arg];
        else if (strcmp(argv[arg], "--help") == 0) { usage(argv[0]); return EXIT_SUCCESS; }
        else { usage(argv[0]); return EXIT_FAILURE; }
    }

    if (channel_hz == 0U || !wav_path) {
        usage(argv[0]);
        return EXIT_FAILURE;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    int device_index = rtlsdr_get_index_by_serial(serial);
    if (device_index < 0 || rtlsdr_open(&device, (uint32_t)device_index) != 0) {
        fprintf(stderr, "Unable to open RTL-SDR serial %s.\n", serial);
        goto cleanup;
    }

    uint32_t tuner_hz = channel_hz + TUNING_OFFSET_HZ;
    if (rtlsdr_set_sample_rate(device, INPUT_RATE_HZ) != 0 ||
        rtlsdr_set_center_freq(device, tuner_hz) != 0 ||
        rtlsdr_set_tuner_gain_mode(device, 1) != 0 ||
        rtlsdr_set_tuner_gain(device, (int)lrintf(gain_db * 10.0f)) != 0 ||
        rtlsdr_reset_buffer(device) != 0) {
        fprintf(stderr, "Unable to configure airband receiver.\n");
        goto cleanup;
    }

    if (wav_open(&writer, wav_path) != 0) goto cleanup;
    buffer = malloc(INPUT_BUFFER_BYTES);
    if (!buffer) goto cleanup;

    printf("Native civil-airband AM receiver with RF activity metric\n");
    printf("  Serial:              %s\n", serial);
    printf("  Channel frequency:   %u Hz\n", channel_hz);
    printf("  Tuner frequency:     %u Hz\n", rtlsdr_get_center_freq(device));
    printf("  Input sample rate:   %u Hz\n", rtlsdr_get_sample_rate(device));
    printf("  Audio sample rate:   %u Hz\n", AUDIO_RATE_HZ);
    printf("  Duration:            %.3f seconds\n", duration_ms / 1000.0);
    fflush(stdout);

    target_samples = ((uint64_t)INPUT_RATE_HZ * (uint64_t)duration_ms) / 1000ULL;

    while (keep_running && total_samples < target_samples) {
        int bytes_read = 0;
        if (rtlsdr_read_sync(device, buffer, INPUT_BUFFER_BYTES, &bytes_read) != 0 || bytes_read <= 0) goto cleanup;
        uint64_t samples = (uint64_t)bytes_read / 2U;
        if (samples > target_samples - total_samples) samples = target_samples - total_samples;

        for (uint64_t n = 0; n < samples; n++) {
            float raw_i = ((float)buffer[n * 2U] - 127.5f) / 127.5f;
            float raw_q = ((float)buffer[n * 2U + 1U] - 127.5f) / 127.5f;
            float shifted_i, shifted_q;

            raw_i_sum += raw_i;
            raw_q_sum += raw_q;
            raw_power_sum += (double)raw_i * raw_i + (double)raw_q * raw_q;

            switch (oscillator_phase & 3U) {
                case 0: shifted_i = raw_i; shifted_q = raw_q; break;
                case 1: shifted_i = -raw_q; shifted_q = raw_i; break;
                case 2: shifted_i = -raw_i; shifted_q = -raw_q; break;
                default: shifted_i = raw_q; shifted_q = -raw_i; break;
            }
            oscillator_phase++;
            sum_i += shifted_i; sum_q += shifted_q; decimation_count++;

            if (decimation_count == DECIMATION_FACTOR) {
                float i = sum_i / (float)DECIMATION_FACTOR;
                float q = sum_q / (float)DECIMATION_FACTOR;
                float magnitude = sqrtf(i * i + q * q);

                chan_power_sum += (double)i * i + (double)q * q;

                if (audio_samples == 0) envelope_dc = magnitude;
                envelope_dc += 0.0020f * (magnitude - envelope_dc);
                float demodulated = magnitude - envelope_dc;
                lowpass_state += 0.55f * (demodulated - lowpass_state);
                float highpass = lowpass_state - hp_prev_input + 0.9620f * hp_prev_output;
                hp_prev_input = lowpass_state;
                hp_prev_output = highpass;

                int16_t pcm = to_pcm16(highpass, audio_gain);
                float normalized = (float)pcm / 32768.0f;
                output_square_sum += (double)normalized * normalized;
                if (fabsf(normalized) > output_peak) output_peak = fabsf(normalized);
                if (wav_write(&writer, pcm) != 0) goto cleanup;

                audio_samples++;
                sum_i = 0.0f; sum_q = 0.0f; decimation_count = 0;
            }
        }
        total_samples += samples;
    }

    wav_close(&writer);

    if (total_samples > 0 && audio_samples > 0) {
        double raw_n = (double)total_samples;
        double chan_n = (double)audio_samples;
        double raw_mean_i = raw_i_sum / raw_n;
        double raw_mean_q = raw_q_sum / raw_n;
        double raw_var = raw_power_sum / raw_n - raw_mean_i * raw_mean_i - raw_mean_q * raw_mean_q;
        /*
         * Keep total decimated channel power, including the complex DC term.
         * After the +Fs/4 frequency shift, an AM carrier is intentionally at
         * baseband DC. Subtracting the channel mean removes that carrier and
         * makes active AM transmissions look like noise.
         */
        double chan_power = chan_power_sum / chan_n;
        double noise_var = raw_var / (double)DECIMATION_FACTOR;
        double signal_excess;
        double rf_snr_db;
        double audio_rms = sqrt(output_square_sum / chan_n);

        if (chan_power < 1.0e-15) chan_power = 1.0e-15;
        if (noise_var < 1.0e-15) noise_var = 1.0e-15;
        signal_excess = chan_power - noise_var;
        rf_snr_db = signal_excess > noise_var * 1.0e-6
            ? 10.0 * log10(signal_excess / noise_var)
            : -30.0;

        printf("\nAM capture metrics\n");
        printf("  Audio samples:       %llu\n", (unsigned long long)audio_samples);
        printf("  Audio duration:      %.3f seconds\n", (double)audio_samples / AUDIO_RATE_HZ);
        printf("  Audio RMS level:     %.2f dBFS\n", 20.0 * log10(audio_rms > 1.0e-12 ? audio_rms : 1.0e-12));
        printf("  Audio peak level:    %.2f dBFS\n", 20.0 * log10(output_peak > 1.0e-12f ? output_peak : 1.0e-12f));
        printf("  RF channel power:    %.2f dBFS\n", 10.0 * log10(chan_power / 2.0));
        printf("  RF noise estimate:   %.2f dBFS\n", 10.0 * log10(noise_var / 2.0));
        printf("  RF estimated SNR:    %.2f dB\n", rf_snr_db);
    }

    exit_code = EXIT_SUCCESS;

cleanup:
    wav_close(&writer);
    free(buffer);
    if (device) rtlsdr_close(device);
    return exit_code;
}
