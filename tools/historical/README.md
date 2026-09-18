# Historical diagnostics

`test_dro_equivalence.py` is preserved from the earlier DRO investigation. It
extracts functions from inline sketch implementations that have since been
replaced by shared headers, so it does not run against the current firmware.
It was already incompatible before the directory reorganization; it is not part
of current test discovery. Current coverage is in `tests/test_dro_firmware.py`
and `tests/absolute_dro_test.cpp`.
