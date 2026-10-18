"""Trial waiver and immutable resume guards; no Blender/render required."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools/volume_capture.py'
spec = importlib.util.spec_from_file_location('volume_capture_gates', HELPER)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CaptureGateRegression(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name)/'capture'
        self.signature = dict(configSha256='a'*64, source=dict(sceneSha256='b'*64))

    def read_trial(self):
        return json.loads((self.output/'trial.json').read_text())

    def test_default_capture_still_requires_trial(self):
        with self.assertRaises(FileNotFoundError):
            capture.prepare_output(self.output, self.signature, 'capture')
        self.assertFalse(self.output.exists())

    def test_override_is_capture_only(self):
        with self.assertRaises(ValueError):
            capture.prepare_output(self.output, self.signature, 'trial', True)
        self.assertFalse(self.output.exists())

    def test_waiver_is_honest_and_resume_requires_explicit_option(self):
        first = capture.prepare_output(self.output, self.signature, 'capture', True)
        self.assertEqual(first, self.read_trial())
        self.assertEqual(first['status'], 'skipped')
        self.assertTrue(first['explicitUserOverride'])
        self.assertEqual(first['probes'], [])
        self.assertEqual(first['source'], self.signature['source'])
        self.assertEqual(capture.prepare_output(self.output, self.signature, 'capture', True), first)
        with self.assertRaises(ValueError):
            capture.prepare_output(self.output, self.signature, 'capture')

    def test_changed_inputs_and_completed_outputs_reject(self):
        capture.prepare_output(self.output, self.signature, 'capture', True)
        for key in ('configSha256', 'source'):
            changed = copy.deepcopy(self.signature)
            changed[key] = 'changed'
            with self.assertRaises(ValueError):
                capture.prepare_output(self.output, changed, 'capture', True)
        (self.output/'volume_receipt.json').write_text('{}')
        with self.assertRaises(ValueError):
            capture.prepare_output(self.output, self.signature, 'capture', True)

    def test_existing_unbound_folder_is_not_adopted(self):
        self.output.mkdir()
        with self.assertRaises(FileNotFoundError):
            capture.prepare_output(self.output, self.signature, 'capture', True)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_default_passed_trial_path_is_preserved(self):
        capture.prepare_output(self.output, self.signature, 'trial')
        trial = dict(status='passed', source=self.signature['source'], probes=[dict(index=[0,0,0], directory='trial_00')])
        (self.output/'trial.json').write_text(json.dumps(trial))
        self.assertEqual(capture.prepare_output(self.output, self.signature, 'capture'), trial)
        with self.assertRaises(ValueError):
            capture.prepare_output(self.output, self.signature, 'capture', True)

    def test_tampered_waiver_rejects(self):
        capture.prepare_output(self.output, self.signature, 'capture', True)
        original = self.read_trial()
        for key, value in (('status', 'passed'), ('explicitUserOverride', False), ('source', {}), ('probes', [dict(index=[0,0,0])])):
            changed = dict(original, **{key:value})
            (self.output/'trial.json').write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                capture.prepare_output(self.output, self.signature, 'capture', True)


if __name__ == '__main__':
    unittest.main()
