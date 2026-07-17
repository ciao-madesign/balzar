"""Test del gate di licenza beta (balzar/license.py). Logica pura file/JSON,
isolata via BALZAR_LICENSE_DIR -- nessuna GUI/browser coinvolti, coerente col
principio del progetto (l'interazione UI si verifica manualmente, non in
unittest). BETA_KEY_SHA256 e' vuoto nel repo (fail-closed): i test lo
impostano temporaneamente a un hash noto e lo ripristinano."""

import os
import tempfile
import unittest

from balzar import license as lic


_KEY = "BETA-TEST-KEY-123"
_HASH = lic._hash_key(_KEY)


class LicenseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_env = os.environ.get("BALZAR_LICENSE_DIR")
        os.environ["BALZAR_LICENSE_DIR"] = self._tmp
        self._old_hash = lic.BETA_KEY_SHA256
        lic.BETA_KEY_SHA256 = _HASH

    def tearDown(self):
        lic.BETA_KEY_SHA256 = self._old_hash
        if self._old_env is None:
            os.environ.pop("BALZAR_LICENSE_DIR", None)
        else:
            os.environ["BALZAR_LICENSE_DIR"] = self._old_env

    # --- verifica chiave -----------------------------------------------
    def test_correct_key_verifies(self):
        self.assertTrue(lic.verify_key(_KEY))

    def test_correct_key_tolerates_surrounding_whitespace(self):
        self.assertTrue(lic.verify_key("  " + _KEY + "\n"))

    def test_wrong_key_rejected(self):
        self.assertFalse(lic.verify_key("nope"))

    def test_unconfigured_gate_rejects_everything(self):
        lic.BETA_KEY_SHA256 = ""
        self.assertFalse(lic.is_configured())
        self.assertFalse(lic.verify_key(_KEY))
        self.assertFalse(lic.is_activated())

    # --- attivazione persistita ----------------------------------------
    def test_activate_persists_and_is_detected(self):
        self.assertFalse(lic.is_activated())
        self.assertTrue(lic.activate(_KEY))
        self.assertTrue(lic.is_activated())

    def test_activate_with_wrong_key_does_not_persist(self):
        self.assertFalse(lic.activate("wrong"))
        self.assertFalse(lic.is_activated())

    def test_activation_invalidated_when_build_key_changes(self):
        self.assertTrue(lic.activate(_KEY))
        self.assertTrue(lic.is_activated())
        # una nuova build con una chiave diversa: la vecchia attivazione non vale
        lic.BETA_KEY_SHA256 = lic._hash_key("A-DIFFERENT-BETA-KEY")
        self.assertFalse(lic.is_activated())

    def test_deactivate_clears_state(self):
        self.assertTrue(lic.activate(_KEY))
        lic.deactivate()
        self.assertFalse(lic.is_activated())

    def test_corrupt_activation_file_is_not_activated(self):
        with open(os.path.join(self._tmp, "activation.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        self.assertFalse(lic.is_activated())

    # --- decisione all'avvio (politica testabile senza Tk) -------------
    def test_startup_dev_build_unconfigured_opens_freely(self):
        lic.BETA_KEY_SHA256 = ""
        self.assertEqual(lic.startup_decision(frozen=False), lic.STARTUP_OPEN)

    def test_startup_packaged_build_without_key_is_refused(self):
        lic.BETA_KEY_SHA256 = ""
        self.assertEqual(lic.startup_decision(frozen=True), lic.STARTUP_UNCONFIGURED)

    def test_startup_configured_not_activated_needs_key(self):
        self.assertEqual(lic.startup_decision(frozen=False), lic.STARTUP_NEED_KEY)
        self.assertEqual(lic.startup_decision(frozen=True), lic.STARTUP_NEED_KEY)

    def test_startup_configured_and_activated_starts(self):
        self.assertTrue(lic.activate(_KEY))
        self.assertEqual(lic.startup_decision(frozen=False), lic.STARTUP_ACTIVATED)


if __name__ == "__main__":
    unittest.main()
