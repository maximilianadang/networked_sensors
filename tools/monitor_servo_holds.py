#!/usr/bin/env python3
"""Read cached dashboard SSE telemetry; never send controller commands.

Print observed servo run intervals. These are sampled electrical command
durations, not shaft-angle measurements or exact browser press timestamps.
Usage: python3 monitor_servo_holds.py
"""
import json
from urllib.request import urlopen


def main():
    previous = None
    start = None
    count = 0
    with urlopen('http://127.0.0.1:8000/api/events', timeout=30) as stream:
        print('MONITOR READY — read-only dashboard telemetry', flush=True)
        for raw in stream:
            if not raw.startswith(b'data: '):
                continue
            sample = json.loads(raw[6:])
            if 'stepper_brushless_motor_on' not in sample:
                continue
            now = sample['elapsed_s']
            on = sample.get('stepper_brushless_motor_on')
            pulse = sample.get('stepper_brushless_motor_pulse_us')
            valid = sample.get('stepper_connected') and sample.get('stepper_age_ms', 9999) <= 300
            if not valid or on not in (True, False):
                if start:
                    print('Telemetry gap — interval cannot be calibrated reliably', flush=True)
                start = previous = None
                continue
            state = pulse if on else None
            if previous is not None and state != previous[1]:
                if start:
                    count += 1
                    lower = previous[0] - start[1]
                    upper = now - start[0]
                    print(f'#{count} STOP {start[2]}: approximately {(lower+upper)/2:.3f} s '
                          f'(sample bracket {lower:.3f}–{upper:.3f} s)', flush=True)
                    start = None
                if on:
                    direction = {1400: 'CW', 1600: 'CCW'}.get(pulse, str(pulse))
                    start = (previous[0], now, direction)
                    print(f'START {direction}, {pulse} us, {sample["timestamp_iso"]}', flush=True)
            previous = (now, state)


if __name__ == '__main__':
    main()
