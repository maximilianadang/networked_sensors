"""Regression tests for protocol-map semantic and source consistency."""

from __future__ import annotations

import unittest

from networked_sensors import protocol_map


class ProtocolMapConsistencyTests(unittest.TestCase):
    def test_current_protocol_passes_semantic_and_source_checks(self) -> None:
        self.assertEqual(protocol_map.validate_protocol(protocol_map.render_protocol()), [])

    def test_current_verification_rejects_volatile_test_counts(self) -> None:
        rendered = protocol_map.render_protocol().replace(
            "No; current suite passes",
            "No; 52 tests passed",
            1,
        )
        errors = protocol_map.validate_protocol(rendered)
        self.assertTrue(
            any("volatile test counts" in error for error in errors),
            errors,
        )

    def test_historical_yun_rows_require_explicit_scope(self) -> None:
        rendered = protocol_map.render_protocol().replace(
            "| Historical Yún T4B compile/upload",
            "| Yún T4B compile/upload",
            1,
        )
        errors = protocol_map.validate_protocol(rendered)
        self.assertTrue(
            any("every historical firmware row" in error for error in errors),
            errors,
        )

    def test_current_yun_build_must_have_one_canonical_value(self) -> None:
        rendered = protocol_map.render_protocol().replace(
            protocol_map.CURRENT_YUN_BUILD,
            "99,999 bytes/99% flash and 9,999 bytes/99% RAM",
            1,
        )
        errors = protocol_map.validate_protocol(rendered)
        self.assertTrue(
            any("canonical current Yún build" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
