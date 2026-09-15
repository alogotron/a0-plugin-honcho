from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

import hooks


class LifecycleHookTests(unittest.TestCase):
    def test_install_migrates_without_starting_worker(self):
        helper = mock.Mock()
        completed = SimpleNamespace(returncode=0)

        with (
            mock.patch.object(hooks, "_helper", return_value=helper),
            mock.patch.object(hooks.subprocess, "run", return_value=completed) as run,
        ):
            hooks.install()

        helper.initialize_runtime.assert_called_once_with(start_worker=False)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [hooks.sys.executable, "-m", "pip", "install"])
        self.assertIn("-r", command)

    def test_install_failure_does_not_initialize_runtime(self):
        helper = mock.Mock()
        failed = SimpleNamespace(returncode=1)

        with (
            mock.patch.object(hooks, "_helper", return_value=helper),
            mock.patch.object(hooks.subprocess, "run", return_value=failed),
        ):
            with self.assertRaises(RuntimeError):
                hooks.install()

        helper.initialize_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
