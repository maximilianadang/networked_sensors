// BLUE CLOCK WIRE MUST BE DISCONNECTED from X1 DO0 before this test.
// PE4 remains INPUT throughout; only its weak internal pull-up is changed.
#include <Arduino.h>
void setup() {
  digitalWrite(7, LOW); pinMode(7, OUTPUT);
  digitalWrite(3, HIGH); pinMode(3, OUTPUT);
  digitalWrite(5, LOW); pinMode(5, OUTPUT);
  digitalWrite(4, LOW); pinMode(4, OUTPUT);
  digitalWrite(27, LOW); pinMode(27, OUTPUT);
  pinMode(2, INPUT); pinMode(6, INPUT);
  TCCR1A = TCCR1B = TIMSK1 = 0;
  TCCR3A = TCCR3B = TIMSK3 = 0;
  EIMSK = PCICR = 0;
  Serial.begin(115200);
}
void loop() {
  static bool pullup = false;
  DDRE &= ~_BV(PE4);
  if (pullup) PORTE |= _BV(PE4); else PORTE &= ~_BV(PE4);
  delay(100);
  byte input = PINE;
  Serial.print(F("{\"pullup\":")); Serial.print(pullup);
  Serial.print(F(",\"clock\":")); Serial.print((input >> PE4) & 1);
  Serial.print(F(",\"digital_read\":")); Serial.print(digitalRead(2));
  Serial.print(F(",\"pine\":")); Serial.print(input);
  Serial.print(F(",\"ddre\":")); Serial.print(DDRE);
  Serial.print(F(",\"porte\":")); Serial.print(PORTE);
  Serial.print(F(",\"mcucr\":")); Serial.print(MCUCR);
  Serial.println('}');
  pullup = !pullup;
  delay(100);
}
