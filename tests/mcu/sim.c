// A pretend board for tests: implements the dv_hw_* shim on the PC so that a sketch's dovod_prog.c
// (the ARDUINO branches of the runtime, the mcu module, the program's main loop) runs under sanitizers.
// Script: the button on pin 2 (active low) is pressed 100..300 ms (short) and 1000..2000 ms (long,
// with 5 ms of contact bounce at the start); the run ends at 2600 ms.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void dovod_main(void);
static long now = 0; static int lamp = -1; static const char* feed = "ping\r\n"; static int said = 0;
void dv_hw_write(const char* p, long n) { printf("%ld say %.*s\n", now, (int)n, p); said++; }
void dv_hw_trap(const char* msg, const char* loc) { printf("TRAP %s (at %s)\n", msg, loc); exit(3); }
long dv_hw_ms(void) { return now; }
void dv_hw_sleep(long ms) { now += ms; if (now >= 2600) { printf("%ld end lamp=%d\n", now, lamp); exit(0); } }
void dv_hw_pin_mode(int pin, int mode) { printf("%ld mode pin%d=%d\n", now, pin, mode); }
void dv_hw_pin_write(int pin, int on) { if (pin == 9 && on != lamp && now < 1700) { lamp = on; printf("%ld lamp %d\n", now, on); } else if (pin == 9) lamp = on; }
int dv_hw_pin_read(int pin) {
    int pressed = (now >= 100 && now < 300) || (now >= 1000 && now < 2000);
    if (now >= 1000 && now < 1005) pressed = (now % 2) == 0;   // bounce
    return pin == 2 ? !pressed : 1;
}
int dv_hw_adc(int pin) { return pin * 10; }
void dv_hw_pwm(int pin, int duty) { printf("%ld pwm pin%d=%d\n", now, pin, duty); }
int dv_hw_getc(void) { return *feed ? *feed++ : -1; }
int main(void) { dovod_main(); return 1; }
