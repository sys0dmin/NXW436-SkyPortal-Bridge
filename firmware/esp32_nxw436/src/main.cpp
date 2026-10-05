#ifdef ARDUINO
#include <Arduino.h>
#include "net/Esp32FakeFrontend.h"

static nxw436::net::Esp32FakeFrontend frontend;

void setup() { frontend.begin(); }
void loop() { frontend.poll(); }
#elif !defined(UNIT_TEST)
int main() { return 0; }
#endif
