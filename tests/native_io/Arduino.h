#pragma once
#include "../native_can/Arduino.h"
#include <type_traits>
using byte = uint8_t;
#define INPUT 0
#define CHANGE 2
#define F_CPU 16000000UL
#define _BV(bit) (1U << (bit))
#define TOV5 0
#define TOIE5 0
#define CS50 0
extern uint8_t TCCR5A, TCCR5B, TIFR5, TIMSK5;
extern uint16_t TCNT5;
int analogRead(uint8_t);
volatile uint8_t *portInputRegister(uint8_t);
uint8_t digitalPinToPort(uint8_t);
uint8_t digitalPinToBitMask(uint8_t);
uint8_t digitalPinToInterrupt(uint8_t);
void attachInterrupt(uint8_t, void (*)(), int);
template <class A, class B>
typename std::common_type<A, B>::type min(A a, B b) { return a < b ? a : b; }
