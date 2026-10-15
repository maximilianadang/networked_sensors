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
#define COM3C1 3
#define COM3C0 2
#define WGM31 1
#define WGM30 0
#define WGM33 4
#define WGM32 3
#define CS31 1
#define CS30 0
#define FOC3C 5
#define TOV3 0
#define OCF3C 3
#define TOIE3 0
#define OCIE3C 3
extern uint8_t TCCR3A, TCCR3B, TCCR3C, TIFR3, TIMSK3;
extern uint16_t TCNT3, OCR3A, OCR3C;
int analogRead(uint8_t);
int digitalRead(uint8_t);
volatile uint8_t *portInputRegister(uint8_t);
uint8_t digitalPinToPort(uint8_t);
uint8_t digitalPinToBitMask(uint8_t);
uint8_t digitalPinToInterrupt(uint8_t);
void attachInterrupt(uint8_t, void (*)(), int);
template <class A, class B>
typename std::common_type<A, B>::type min(A a, B b) { return a < b ? a : b; }
