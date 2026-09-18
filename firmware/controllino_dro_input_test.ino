// Temporary read-only DRO isolation test. Restore controllino_motion_control.ino.
// No Ethernet, Servo library, Timer1/3 configuration, or motion commands.
#include <Arduino.h>
#include <util/atomic.h>
volatile unsigned long clockEdges = 0;
void clockFall() { ++clockEdges; }
void setup() {
  digitalWrite(7, LOW); pinMode(7, OUTPUT);   // stepper disabled
  digitalWrite(3, HIGH); pinMode(3, OUTPUT); // STEP idle
  digitalWrite(5, LOW); pinMode(5, OUTPUT);  // direction static
  digitalWrite(4, LOW); pinMode(4, OUTPUT);  // servo signal off
  digitalWrite(27, LOW); pinMode(27, OUTPUT); // R5 off
  pinMode(2, INPUT); pinMode(6, INPUT);
  attachInterrupt(digitalPinToInterrupt(2), clockFall, FALLING);
  Serial.begin(115200);
}
void loop() {
  unsigned long edges;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { edges = clockEdges; }
  Serial.print(F("{\"clock\":")); Serial.print((PINE >> PE4) & 1);
  Serial.print(F(",\"data\":")); Serial.print((PINH >> PH3) & 1);
  Serial.print(F(",\"ddre\":")); Serial.print(DDRE);
  Serial.print(F(",\"eimsk\":")); Serial.print(EIMSK);
  Serial.print(F(",\"eicrb\":")); Serial.print(EICRB);
  Serial.print(F(",\"edges\":")); Serial.print(edges);
  Serial.println('}');
  delay(200);
}
