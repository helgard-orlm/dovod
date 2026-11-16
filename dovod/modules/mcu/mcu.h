// Module `mcu`, native side. The hardware itself is reached through dv_hw_* — plain C functions that the
// sketch's shim (the .ino written by `dovod build --target arduino`) implements with the Arduino API.
void dv_hw_sleep(long ms);
void dv_hw_pin_mode(int pin, int mode);   // 0 = output, 1 = input, 2 = input with pull-up
void dv_hw_pin_write(int pin, int on);
int dv_hw_pin_read(int pin);
int dv_hw_adc(int pin);
void dv_hw_pwm(int pin, int duty);
int dv_hw_getc(void);                     // next byte from the serial port, -1 if none

static void dv_mcu_pin_out(int64_t pin) { dv_hw_pin_mode((int)pin, 0); }
static void dv_mcu_pin_in(int64_t pin, bool pullup) { dv_hw_pin_mode((int)pin, pullup ? 2 : 1); }
static void dv_mcu_pin_set(int64_t pin, bool on) { dv_hw_pin_write((int)pin, on ? 1 : 0); }
static bool dv_mcu_pin_get(int64_t pin) { return dv_hw_pin_read((int)pin) != 0; }
static int64_t dv_mcu_adc(int64_t pin) { return (int64_t)dv_hw_adc((int)pin); }
static void dv_mcu_pwm(int64_t pin, int64_t duty) { dv_hw_pwm((int)pin, (int)duty); }
static void dv_mcu_sleep(int64_t ms) { dv_hw_sleep((long)ms); }
static int64_t dv_mcu_ms(void) { return (int64_t)(uint32_t)dv_hw_ms(); }
static void dv_mcu_say(dv_text line) { dv_hw_write(line.p, (long)line.n); }
// collects bytes into one fixed buffer; a line longer than the buffer is cut
static O_Text dv_mcu_hear(void) {
    static char buf[96]; static int n = 0;
    O_Text r = {0};
    for (;;) {
        int c = dv_hw_getc();
        if (c < 0) return r;
        if (c == '\r') continue;
        if (c == '\n') { r.has = true; r.v = dv_text_new(buf, n); n = 0; return r; }
        if (n < (int)sizeof buf) buf[n++] = (char)c;
    }
}
