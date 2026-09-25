from __future__ import annotations

import unittest

from nxw436_az_wrap_recover import MAX_ABOVE_MODULO_COUNTS, is_recoverable_above_modulo
from nxw436_driver import RAW_MODULO


class AzWrapRecoveryTests(unittest.TestCase):
    def test_only_narrow_above_modulo_range_is_recoverable(self):
        self.assertTrue(is_recoverable_above_modulo(RAW_MODULO))
        self.assertTrue(is_recoverable_above_modulo(RAW_MODULO + 1))
        self.assertTrue(is_recoverable_above_modulo(RAW_MODULO + MAX_ABOVE_MODULO_COUNTS))
        self.assertFalse(is_recoverable_above_modulo(RAW_MODULO - 1))
        self.assertFalse(is_recoverable_above_modulo(RAW_MODULO + MAX_ABOVE_MODULO_COUNTS + 1))


if __name__ == "__main__":
    unittest.main()
