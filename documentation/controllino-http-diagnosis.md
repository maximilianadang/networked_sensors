# Controllino Stop timeout investigation

**Status: revised candidate uploaded; physical Stop acceptance pending.**
The latest build passes the communication tests below. Earlier candidates
failed after short passing runs, so those failures are retained in this log.
Do not treat passing stopped-controller tests as proof of physical stopping.

## Baseline and scope

The operator observed continuous motion continuing after a Stop request timed
out. Read-only tests subsequently reproduced timeouts while the controller was
stopped and disabled: 3 of 12 status requests failed in one run. A separate
socket-level test isolated a timeout to TCP connection establishment, before
an HTTP request could be sent. This does not prove the cause of the original
Stop failure.

Successful 361-byte HTTP responses commonly took about 96 ms to receive and
arrived in approximately 348 application reads. Read boundaries are not packet
boundaries. Source inspection independently confirmed that Arduino's flash-
string Print overload emits individual bytes, and EthernetClient synchronously
sends each write. Prompt TCP acknowledgements did not remove the roughly 96 ms
baseline in the comparison test.

## First change: uploaded; faster replies, connection failures persist

`controllino_motion_control.ino` now formats the same HTTP header and status or
acknowledgement into a bounds-checked 512-byte static Print buffer. The complete
response is passed to EthernetClient in one write. Overflow returns HTTP 500
instead of partial JSON. A failed acknowledgement is not proof that a command
was not executed; motor commands must not be automatically replayed.

The STEP/ESC timers, pin mapping, command parser, serial reporting, Ethernet
close behavior, and dashboard timeout remain unchanged. No watchdog was added.

The Controllino compile succeeded: 21,442 bytes flash and 1,576 bytes static
SRAM, leaving 6,616 bytes for local variables. The native C++ regression test
executes the sketch's formatter and buffering code with a counting Print sink:
normal/extreme status and acknowledgement output must match byte-for-byte,
use one write, reject overflow, and recover on the next response.

## Hardware validation gate

USB was initially absent. Once connected, the change was uploaded on
2026-09-14 to `/dev/ttyACM0` through `firmware_upload.compile_and_upload`, with
the explicit `controllino_motion_control.ino` payload and upload verification.
Uploaded source SHA-256:
`97e326feb64edab7520ba46b7f96ad93e151fa2b0c80ad4bdfddfbbd1eb23f89`.
The full regression suite passed (111 tests). Startup status confirmed zero
pulse position, stopped motion, and disabled driver.

Read-only post-upload tests used the existing 750 ms socket timeout, a 350 ms
gap between batches, and the normally running dashboard:

| Test | Requests | Failures | Successful median | Successful maximum |
| --- | ---: | ---: | ---: | ---: |
| Sequential additional reader | 20 | 4 | 10.65 ms | 21.22 ms |
| Paired additional readers | 20 | 12 | 10.595 ms | 10.84 ms |

All failures occurred during TCP connection establishment. Successful responses
arrived in one application read, and every observed status retained zero pulse
position, stopped motion, and disabled driver. No motion commands were sent.
The dashboard reported connected/stopped with no current transport error after
the test, demonstrating that a healthy final snapshot does not rule out the
intermittent failures.

Conclusion: buffering improves successful response latency but does not solve
the Stop-path transport failure. Paired-reader failures warrant investigation
of listener/socket replenishment and overlapping polling/command connections.
At this stage, serial reporting remained unchanged.

Remaining validation steps for a subsequent fix:

1. Verify stopped/disabled startup and unchanged status schema.
2. Repeat read-only status timing tests at the existing 750 ms timeout, with
   the normal dashboard poller running. Include overlapping status requests.
3. Compare connection failures and response latency with the baseline above.
   Fast replies alone do not establish reliable Stop delivery.
4. Validate Stop under controlled, operator-supervised conditions with physical
   power removal available. Do not resume unattended continuous motion.

Only after assessing this isolated change should serial blocking be addressed
as a separate experiment. At 9600 baud, the observed 266-byte status requires
about 277 ms on the wire and can block its caller for approximately 210 ms
with the standard 64-byte TX buffer. The sketch waits 100 ms after completing
the write during motion, rather than producing an unbounded 100 ms backlog.

## Subsequent experiments (2026-09-14)

The installed Ethernet library's `EthernetServer::available()` creates a
listener only when it finds none. WIZnet hardware has a finite socket pool;
overlapping requests can arrive before a replacement listener exists. Reserving
three listeners at startup and replenishing after each request produced 0/60
failures for paired reads and 0/60 for paired Stop/read requests, with the
dashboard polling throughout. This supported proceeding to the separately
authorized serial-reporting change, but did not establish long-term reliability.

Serial telemetry and USB acknowledgements now share a fixed 512-byte FIFO.
The main loop sends only bytes that fit `Serial.availableForWrite()`. Periodic
reports are coalesced while the queue is occupied; USB input receives
backpressure when there is insufficient ACK space. Baud rate and JSON schema
are unchanged. Native tests exercise zero TX capacity, partial drains, frame
ordering, buffer overflow, and byte-for-byte HTTP formatting.

With three listeners and queued serial output, a 150-request test had one
connection timeout; 15 complete dashboard Stop requests succeeded while already
stopped. Four startup listeners were then tested. Source SHA-256:
`66443703509c4a38b1fe1b7d28f90d712e86b154fd23456f8d2610142baeca2d`.
Compile: 21,668 bytes flash, 2,095 bytes static SRAM (6,097 bytes remaining).
The regression suite passes 113 tests.

A four-listener run after USB reset completed 300 requests with zero failures,
median 21.675 ms and maximum 44.17 ms at the unchanged 750 ms timeout.
However, a subsequent status request again timed out during connection
establishment. The dashboard also reported stale telemetry, and a direct
five-second connection attempt failed. Ethernet link and ARP remained present.
Opening USB reset the controller and restored communication. Therefore the
listener change alone must not be presented as a complete fix.

Temporary USB trace markers were used to distinguish stalls in response
write (`W`), close (`C`), or listener replenishment (`B`), enclosed in `[...]`.
These deliberately diagnostic markers have now been removed, including the
temporary timer-interrupt heartbeat. No start, enable, or motion command was sent during these
experiments; Stop acknowledgements while stopped do not validate physical
stopping during motion.

The early-disconnect diagnostic reproduced a transient connection timeout,
but serial trace continued and a subsequent request succeeded. This is not
evidence of a permanent main-loop lockup. USB DTR toggling restored a stalled
controller by resetting it; toggling DTR off did not consistently cause a
failure, so reset-line behavior has not been established as the cause.

## Serial isolation and capacity snapshot

With periodic serial telemetry temporarily disabled (no other command/timer
change), 300 concurrent requests passed, median 21.64 ms, maximum 41.65 ms.
All 20 subsequent status checks passed too. This was an isolation experiment,
not a proposed removal of USB functionality.

Review identified a changing expression passed to Arduino's `min` macro:
`min(serialOutput.length, size_t(Serial.availableForWrite()))`. Unlike
`std::min`, that macro may sample capacity twice. The capacity is now stored
once before applying `min`. The native test now uses Arduino's macro semantics
and asserts exactly one capacity read per drain. This corrects a race-prone
expression; it does not by itself prove the cause of the observed outage.

Normal telemetry is restored. Uploaded source SHA-256:
`9bce48cc73ffbe7a034eaff9651efe64d6817589f5d265a7df9ba8ec387e1432`.
Compile: 21,652 bytes flash, 2,095 bytes static SRAM. All 113 regression tests
pass.

Post-upload validation, with normal dashboard polling and no start/enable
commands:

- 300 concurrent requests (100 Stop acknowledgements and 200 status reads):
  zero failures, median 21.64 ms, maximum 44.61 ms, original 750 ms timeout.
- 20 Stop requests through the actual dashboard `/api/stepper/local-run`
  endpoint: all HTTP requests succeeded, median 99.395 ms, maximum 103.14 ms.
- A further 60 dashboard Stop requests, explicitly asserting each response
  confirmed stopped motion and disabled driver: all passed, median 99.485 ms,
  maximum 200 ms. The final direct status read also succeeded in 17.15 ms.
- Fresh 9600-baud USB capture contained five complete, readable status frames
  showing stopped/disabled state. An initial unflushed capture contained an
  invalid byte; flushing the laptop input buffer before the fresh capture
  removed that issue. A partial first frame is expected when attaching midway
  through a telemetry stream.

The dashboard timeout was not increased, commands are not automatically
retried, and STEP/ESC timing, pin mapping and command semantics are unchanged.
No communication-loss watchdog has been introduced. The next acceptance gate
is an operator-supervised short move followed by Stop, with physical power
removal available. All Stop tests so far were performed while already stopped.
The earlier persistent outage did not recur in this latest validation;
its precise causal attribution remains unproven.
