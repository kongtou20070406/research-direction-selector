"""Campaign discovery must reject non-regular marker inputs before decoding."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_campaign as campaign


class CampaignMarkerInputTests(unittest.TestCase):
    def test_required_device_marker_refuses_before_decode(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {campaign.ENVIRONMENT: str(Path(os.devnull).absolute())}), \
                    patch.object(campaign, '_decode', side_effect=AssertionError('no decode')) as decode:
                with self.assertRaisesRegex(ValueError, 'regular file'):
                    campaign.binding(temporary)
            decode.assert_not_called()

    def test_marker_exact_limit_and_oversize_before_decode(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / campaign.MARKER
            raw = b' ' * campaign.MAX_BYTES
            target.write_bytes(raw)
            with patch.object(campaign, '_decode', return_value={'control': True}) as decode:
                self.assertEqual(campaign._read(target), {'control': True})
                decode.assert_called_once_with(raw)
            target.write_bytes(raw + b' ')
            with patch.object(campaign, '_decode', side_effect=AssertionError('no decode')) as decode:
                with self.assertRaisesRegex(ValueError, 'Campaign binding exceeds 16 KiB'):
                    campaign._read(target)
            decode.assert_not_called()
            self.assertEqual(target.read_bytes(), raw + b' ')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO unavailable')
    def test_actual_ancestor_and_required_fifo_refuse_without_writer(self):
        code = "import sys;sys.path.insert(0,sys.argv[1]);import rds_campaign as c;\ntry:c.binding(sys.argv[2])\nexcept ValueError as e:assert 'regular file' in str(e),str(e)\nelse:raise AssertionError('nonregular marker accepted')"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = root / 'child'
            child.mkdir()
            marker = root / campaign.MARKER
            os.mkfifo(marker)
            env = os.environ.copy()
            env.pop(campaign.ENVIRONMENT, None)
            for required in (False, True):
                if required:
                    marker.unlink()
                    marker = root / 'required-fifo'
                    os.mkfifo(marker)
                    env[campaign.ENVIRONMENT] = str(marker)
                result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts'), str(child)], env=env, capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
