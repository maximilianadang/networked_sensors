// Input-only isolation: no STEP/servo timers, Ethernet, or external interrupts.
// Every capture window polls registers with ALL interrupts disabled. UART
// reporting happens only between windows. Restore motion firmware after use.
#include <Arduino.h>

void setup() {
  digitalWrite(7, LOW); pinMode(7, OUTPUT);    // driver disabled
  digitalWrite(3, HIGH); pinMode(3, OUTPUT);   // STEP idle
  digitalWrite(5, LOW); pinMode(5, OUTPUT);    // direction static
  digitalWrite(4, LOW); pinMode(4, OUTPUT);    // servo signal off
  digitalWrite(27, LOW); pinMode(27, OUTPUT);  // relay off
  pinMode(2, INPUT); pinMode(6, INPUT);
  TCCR1A = TCCR1B = TIMSK1 = 0;
  TCCR3A = TCCR3B = TIMSK3 = 0;
  EIMSK = PCICR = 0;
  Serial.begin(115200);
}

void loop() {
  Serial.flush();
  byte saved = SREG;
  cli();
  byte lastE = PINE, lastH = PINH, seenE = lastE, allE = lastE;
  byte seenH = lastH, allH = lastH;
  unsigned long clockChanges = 0, dataChanges = 0;
  for (unsigned long i = 0; i < 1000000UL; ++i) {
    byte e = PINE, h = PINH;
    if ((e ^ lastE) & _BV(PE4)) ++clockChanges;
    if ((h ^ lastH) & _BV(PH3)) ++dataChanges;
    seenE |= e; allE &= e; seenH |= h; allH &= h;
    lastE = e; lastH = h;
  }
  SREG = saved;
  Serial.print(F("{\"clock_changes\":")); Serial.print(clockChanges);
  Serial.print(F(",\"data_changes\":")); Serial.print(dataChanges);
  Serial.print(F(",\"pine_any_high\":")); Serial.print(seenE);
  Serial.print(F(",\"pine_always_high\":")); Serial.print(allE);
  Serial.print(F(",\"pinh_any_high\":")); Serial.print(seenH);
  Serial.print(F(",\"pinh_always_high\":")); Serial.print(allH);
  Serial.print(F(",\"ddre\":")); Serial.print(DDRE);
  Serial.print(F(",\"ddrh\":")); Serial.print(DDRH);
  Serial.println('}');
}
