# Controllino rollback — 2026-09-15

## Dashboard/codebase restoration

The recovered transcript immediately before the first successful DRO report
shows only three tracked modified files: the Controllino sketch and the two
test files. All tracked dashboard, transport, runtime, browser and protocol
files have therefore been restored to commit `46e27c2`. The sketch remains the
hash-matched pre-servo version described below. Later servo/relay-specific
tests were removed from the active suite; pre-servo DRO and transport tests
remain. Their formatting/support declarations are not claimed byte-identical
to the historical test files. 116 tests pass, including the additional,
retained native Yún/Controllino DRO equivalence test.

Before this rollback, all tracked differences from `46e27c2` were saved in
`2026-09-15-before-codebase-rollback.patch`. This is a recovery patch against
that commit, not a patch to apply blindly to an arbitrary working tree.
Untracked diagnostics, evidence, user-created DRO-only sketches, configuration,
and recordings were preserved. No reset, commit, push or dashboard restart was
performed. This is a production-source restoration, not deletion of later
diagnostic evidence or an exact historical filesystem snapshot.

Behavior restored includes old brushless UI and ESP32 Solenoid 4 routing;
there is no Controllino servo or R5 support in this baseline. Restart any
running dashboard before testing the restored source. The blue DRO wire was
last confirmed disconnected for the input test; reconnect to X1 DO0 with power
removed before expecting DRO frames. This rollback itself does not establish
that the missing-clock problem is solved.

The current servo-capable firmware is preserved byte-for-byte in
`2026-09-15-controllino-before-dro-rollback.ino`, SHA-256
`0f032ab99032e1f8939d75ea01681b2711dcf64adc25d2030c071f491f7eed97`.

At the user's request, `../../controllino_motion_control.ino` was restored to
the exact pre-servo source, SHA-256
`39df65c17de8edc42857d5713fc334b727e35a316c244c8a84f0d954cc030f6a`.
It was recovered by reversing later edits and matching the complete source
hash against the historical successful upload record in the September 14
session transcript (recorded 2026-09-14T23:30:29.693Z). It is not an approximate
diagnostic reconstruction. Prior audit statements that no exact rollback was
recoverable are superseded by this recovery.

Uploaded through `firmware_upload.compile_and_upload`, Controllino MAXI
Automation target, USB `/dev/ttyACM1`, verified board identity
`usb-Arduino__www.arduino.cc__0042_0353534333535140E124-if00`.
Compile: 23,750 bytes flash and 2,155 bytes SRAM, matching the historical build.
The uploader completed its `--verify` operation successfully.

Ten subsequent Ethernet status samples: dq=0, dce=0, dcl=0 throughout;
ddl took 0 and 1; df=0; mv=en=bo=0. No motion commands were sent.
The missing-clock symptom persists with the exact pre-servo firmware.

This firmware remains installed. DRO uses X1 DO0 clock / DO4 data. Auxiliary
output is the old ESC on Arduino D12, not the servo on X1 DO2. It has no
servo capability or R5 command support. Modern servo-specific tests are not
expected to pass against this deliberately historical main sketch; their code
has not been weakened or rolled back to conceal that difference.

## Direct polling isolation

Ran `controllino_dro_poll_test.ino` using `tools/check_dro_firmware_isolation.py`
with the verified USB identity and a separate result path. Seven windows of
one million register samples each counted zero PE4 clock transitions and
1,241 PH3 data transitions. All interrupts were disabled within each window;
Timer1/Timer3 and external interrupts were disabled, with UART reporting only
between windows. PE4 never read HIGH (PINE OR and AND masks both 227).
See `../dro-poll-isolation-results.json`. This reproduces the missing input
without the interrupt handler or DRO decoder, but does not prove which part
of the physical input path or mapping is responsible. Controlled input-level
testing is still needed. The exact pre-servo firmware above was automatically
restored and upload verification succeeded afterward.

## Disconnected-clock weak pull-up test

User disconnected blue from X1 DO0 with power removed, then repowered.
`controllino_dro_pullup_test.ino` kept PE4 input and alternated its internal
pull-up. All 54 samples read LOW via both PINE and digitalRead(2).
DDRE=40 throughout; PORTE=48 with pull-up on, 32 off; MCUCR=0 (PUD clear).
Results: `../dro-pullup-isolation-results.json`. The external blue wire is
therefore not the sole explanation for LOW. A weak pull-up cannot distinguish
onboard loading from an input fault; a controlled external input-level test
is still required. The pre-servo sketch was restored with upload verification.
