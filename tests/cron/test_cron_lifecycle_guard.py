import unittest
from cron.prompt_scan import _scan_cron_lifecycle_prompt

class CronLifecycleGuardTests(unittest.TestCase):
    def test_direct_restart_is_blocked(self):
        self.assertIn('Blocked', _scan_cron_lifecycle_prompt('Run hermes gateway restart after idle.'))

    def test_natural_language_restart_is_blocked(self):
        self.assertIn('Blocked', _scan_cron_lifecycle_prompt('Restart the gateway after the upgrade.'))

    def test_queued_restart_is_blocked(self):
        self.assertIn('Blocked', _scan_cron_lifecycle_prompt('Queue an external restart request, then verify the restart receipt.'))

    def test_receipt_reporting_is_allowed(self):
        self.assertEqual('', _scan_cron_lifecycle_prompt('Read and report the durable restart receipt only.'))

    def test_unrelated_cron_is_allowed(self):
        self.assertEqual('', _scan_cron_lifecycle_prompt('Summarize current gateway health and platform status.'))

if __name__ == '__main__':
    unittest.main()
