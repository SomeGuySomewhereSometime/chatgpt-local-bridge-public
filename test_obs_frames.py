import base64
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import obs_frames
from obs_control import OBSError


class FrameArgumentTests(unittest.TestCase):
    def test_limits_reject_before_opening_or_decoding(self):
        for kwargs in ({'count': 0}, {'count': 7}, {'count': True},
                       {'start_seconds': -1}, {'start_seconds': math.nan},
                       {'start_frame': -1}, {'start_frame': True}, {'start_frame': 1.5}, {'interval_seconds': 0}, {'interval_seconds': math.inf}):
            with self.subTest(kwargs=kwargs), patch('obs_frames.os.open') as opened:
                with self.assertRaises(OBSError):
                    obs_frames.extract_frames('/video.mkv', Path, **kwargs)
                opened.assert_not_called()

    def test_path_denial_precedes_decoder(self):
        def denied(_):
            raise HTTPException(403, 'DENIED')
        with patch('obs_frames._decode') as decode, self.assertRaises(HTTPException):
            obs_frames.extract_frames('/private/video.mkv', denied)
        decode.assert_not_called()

    def test_mcp_error_is_structured(self):
        import mcp_server
        with patch.object(mcp_server.bridge, 'safe_path', side_effect=HTTPException(403, 'secret-path')):
            result = mcp_server.obs_extract_frames('/private/video.mkv')
        self.assertTrue(result.is_error)
        self.assertNotIn('secret-path', result.content[0].text)


@unittest.skipUnless(os.environ.get('BRIDGE_HOST_TESTS') == '1', 'explicit host decoder test')
class FrameDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='bridge-frames-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.video = Path(cls.tmp.name) / 'motion.mkv'
        subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=320x180:rate=10:duration=3', '-c:v', 'libx264',
                        '-threads', '1', '-an', str(cls.video)], check=True, timeout=10)

    def test_real_decode_returns_distinct_native_frames_without_duplicate_base64(self):
        import mcp_server
        with patch.object(mcp_server.bridge, 'safe_path', side_effect=Path):
            result = mcp_server.obs_extract_frames(str(self.video))
        self.assertFalse(result.is_error, result.content)
        images = [c for c in result.content if c.type == 'image']
        self.assertEqual(len(images), 3)
        self.assertEqual(len({image.data for image in images}), 3)
        self.assertTrue(all(base64.b64decode(image.data).startswith(b'\xff\xd8') for image in images))
        self.assertEqual([f['requested_time_seconds'] for f in result.structured_content['frames']], [0, 1, 2])
        self.assertTrue(all('data' not in frame for frame in result.structured_content['frames']))

    def test_consecutive_frame_indices_decode_distinct_images(self):
        # 60 fps source proves that sub-0.1-second changes are not skipped.
        video = Path(self.tmp.name) / 'sixty-fps.mkv'
        subprocess.run(['/usr/bin/ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=320x180:rate=60:duration=1', '-c:v', 'libx264',
                        '-threads', '1', '-an', str(video)], check=True, timeout=10)
        result = obs_frames.extract_frames(str(video), Path, count=6, start_frame=20)
        self.assertEqual(result['sampling_mode'], 'consecutive_frames')
        self.assertEqual([f['source_frame_index'] for f in result['frames']], list(range(20, 26)))
        self.assertTrue(all(f['requested_time_seconds'] is None for f in result['frames']))
        self.assertEqual(len({f['data'] for f in result['frames']}), 6)

    def test_out_of_range_does_not_return_repeated_last_frames(self):
        with self.assertRaisesRegex(OBSError, 'before duration'):
            obs_frames.extract_frames(str(self.video), Path, start_seconds=2, count=3)

    def test_broken_video_is_rejected(self):
        broken = Path(self.tmp.name) / 'broken.mkv'
        broken.write_bytes(b'not a video')
        with self.assertRaises(OBSError):
            obs_frames.extract_frames(str(broken), Path)

    def test_changed_recording_is_rejected(self):
        copy = Path(self.tmp.name) / 'changing.mkv'
        copy.write_bytes(self.video.read_bytes())
        original = obs_frames._decode
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[1][0] == '/usr/bin/ffmpeg':
                with copy.open('ab') as stream:
                    stream.write(b'changed')
            return result
        with patch('obs_frames._decode', side_effect=change), self.assertRaisesRegex(OBSError, 'changed'):
            obs_frames.extract_frames(str(copy), Path, count=1)

    def test_decoder_reads_verified_descriptor_and_keeps_resource_limits(self):
        original = obs_frames.subprocess.run
        recorded = []
        def inspect(argv, **kwargs):
            recorded.append(argv)
            return original(argv, **kwargs)
        with patch.object(obs_frames.subprocess, 'run', side_effect=inspect):
            result = obs_frames.extract_frames(str(self.video), Path, count=1)
        self.assertEqual(len(result['frames']), 1)
        for argv in recorded:
            self.assertEqual(argv[0], '/usr/bin/prlimit')
            self.assertIn('--cpu=8', argv)
            self.assertTrue(any(value.startswith('/proc/self/fd/') for value in argv))
            self.assertNotIn('/input/video', argv)
            self.assertNotIn('/usr/bin/bwrap', argv)


if __name__ == '__main__':
    unittest.main()
