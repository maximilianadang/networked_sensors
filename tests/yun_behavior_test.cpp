// Run the real sketch against fake inputs/ports. Timer edges are advanced explicitly.
#include <cassert>
#include "Arduino.h"
#include "limit_switch_palas.ino"

void command(const char *text, CommandTransport owner=TRANSPORT_USB) {
  char buffer[64]; strcpy(buffer, text); processCommand(buffer, owner);
}
void tick(unsigned long us=1000) {
  nowUs += us; loop();
  // Stable behavioral trace also permits before/after refactor comparisons.
  printf("%d %d %s %ld %d %d %d %d\n", int(controlMode), int(motionState),
         motionReason, currentPulsePosition(), int(pulseEngineIsEnabled()),
         int(driverOutputEnabled), int(positiveLimitLatched), int(negativeLimitLatched));
}
int main() {
  std::fill(pins, pins+32, HIGH);
  pins[PIN_RUN]=LOW;
  setup();
  assert(!pulseEngineIsEnabled() && !driverOutputEnabled);
  tick(201000); assert(!pulseEngineIsEnabled() && !d4OffObservedSinceBoot);
  pins[PIN_RUN]=HIGH; tick(); assert(d4OffObservedSinceBoot);
  command("V1 M1"); assert(lastCommandAccepted && controlMode == WEB_POSITION);
  command("V1 G10,378,1"); assert(!lastCommandAccepted); // D4 off
  pins[PIN_RUN] = LOW;
  command("V1 G10,378,1"); assert(lastCommandAccepted && activeWebMotion);
  tick(201000); assert(pulseEngineIsEnabled());
  for (int i=0; i<10; ++i) TIMER1_COMPA_vect();
  assert(!pulseEngineIsEnabled() && currentPulsePosition()==10);
  tick(); assert(motionState==STATE_WEB_COMPLETED && !activeWebMotion);
  command("V1 G10,378,2"); tick(201000);
  pins[PIN_DIR] = LOW; tick(); tick(9000);
  assert(activeWebMotion); // brief selector edge must not abort
  tick(1000); assert(!activeWebMotion && !strcmp(motionReason,"direction_auth"));
  // A qualified opposite-end latch must clear during departure, not just at acceptance.
  positiveLimitLatched = true;
  command("V1 G-10,378,3"); assert(lastCommandAccepted);
  positiveLimitLatched = true; tick(); assert(!positiveLimitLatched);
  pins[PIN_LIMIT_NEG] = LOW; tick(); tick(4999); assert(activeWebMotion);
  tick(1); assert(!activeWebMotion && motionState==STATE_LIMIT_BLOCKED);
  command("V1 H"); assert(lastCommandAccepted && positionHomed);
  assert(!strcmp(motionReason,"home_complete"));
  command("V1 B1"); assert(brushlessMotorOn);
  command("V1 E1", TRANSPORT_NETWORK);
  assert(emergencyStopLatched && !brushlessMotorOn && !pulseEngineIsEnabled());
  command("V1 E0"); assert(!lastCommandAccepted);
  pins[PIN_RUN]=HIGH; command("V1 E0"); assert(lastCommandAccepted);
  pins[PIN_LIMIT_NEG]=HIGH; pins[PIN_DIR]=HIGH; tick(); tick(11000);
  command("V1 M0"); pins[PIN_RUN]=LOW; tick(); tick(201000);
  assert(pulseEngineIsEnabled());
  pins[PIN_RUN]=HIGH; tick(); assert(!pulseEngineIsEnabled());
  command("V1 M1"); pins[PIN_DIR]=LOW; tick(); tick(11000);
  pins[PIN_RUN]=LOW; command("V1 H"); tick(201000);
  assert(homingMotion && pulseEngineIsEnabled());
  for (long i=0; i<MAX_HOME_SEARCH_STEPS; ++i) TIMER1_COMPA_vect();
  tick(); assert(!activeWebMotion && !strcmp(motionReason,"home_search_exhausted"));
  command("V1 G-10,378,4"); tick(201000);
  pins[PIN_RUN]=HIGH; tick(); assert(!activeWebMotion && !strcmp(motionReason,"d4_abort"));
  // Ownership rejects competing mutations, but stop/estop semantics stay independent.
  command("V1 S500",TRANSPORT_NETWORK); assert(!lastCommandAccepted);
  tick(2001000); command("V1 S500",TRANSPORT_NETWORK); assert(lastCommandAccepted);
  // Same CR/LF framing and 20-byte service budget for both ports.
  Serial.input="V1 E1\r\n"; serviceTransports(); assert(emergencyStopLatched);
  Serial1.input="V1 E0\n"; serviceTransports(); assert(!emergencyStopLatched);
  Serial.input=std::string(50,'z'); serviceTransports(); assert(Serial.input.size()==30);
  serviceTransports(); serviceTransports(); assert(!lastCommandAccepted);
  Serial1.input=std::string(48,'z');
  serviceTransports(); serviceTransports(); serviceTransports();
  assert(!strcmp(lastCommandError,"line_too_long"));
  // USB never fills the endpoint exactly; disconnected USB releases its shared frame.
  strcpy(networkTxActive,"abcdef"); networkTxActiveLength=6;
  networkTxActiveSharedWithUsb=true; usbTxActiveOffset=0;
  Serial.capacity=1; flushUsbOutput(); assert(usbTxActiveOffset==0);
  Serial.capacity=4; flushUsbOutput(); assert(usbTxActiveOffset==3);
  Serial.connected=false; flushUsbOutput(); assert(usbTxActiveOffset==6);
  puts("Yun behavior passed");
}
