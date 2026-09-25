from __future__ import annotations

import unittest

from nxw436_uart_burst_characterize import analyze_frames, changed_bit_positions, modal_valid_frame


class UartBurstAnalysisTests(unittest.TestCase):
    def test_modal_xor_hamming_and_timeout_are_retained(self):
        baseline = bytes.fromhex("000001")
        frames = [baseline, baseline, bytes.fromhex("000009"), None, bytes.fromhex("200001")]
        modal = modal_valid_frame(frames)
        self.assertEqual(modal, baseline)
        rows = analyze_frames(frames, modal)
        self.assertEqual(rows[2]["xor_to_modal_hex"], "000008")
        self.assertEqual(rows[2]["hamming_distance"], 1)
        self.assertEqual(rows[2]["changed_bit_positions_lsb0"], "3")
        self.assertEqual(rows[3]["reply_kind"], "timeout")
        self.assertEqual(rows[4]["reply_kind"], "malformed-outside-modulus")
        self.assertEqual(rows[4]["hamming_distance"], 1)

    def test_modal_uses_only_valid_position_frames(self):
        valid = bytes.fromhex("000001")
        malformed = bytes.fromhex("200001")
        self.assertEqual(modal_valid_frame([malformed, malformed, valid]), valid)

    def test_changed_bits_are_lsb_indexed(self):
        self.assertEqual(changed_bit_positions(0x800801), "0,11,23")


if __name__ == "__main__":
    unittest.main()
