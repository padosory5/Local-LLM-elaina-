import unittest

import numpy as np

from voice.audio_processing import RollingAudioBuffer, resample_capture_chunk


class VadAudioTests(unittest.TestCase):
    def test_resamples_native_capture_to_silero_chunk(self):
        source = np.linspace(-0.5, 0.5, num=1536, dtype=np.float32)

        result = resample_capture_chunk(source, target_samples=512)

        self.assertEqual(result.dtype, np.int16)
        self.assertEqual(result.shape, (512,))
        self.assertLess(int(result[0]), 0)
        self.assertGreater(int(result[-1]), 0)

    def test_rolling_buffer_keeps_newest_microphone_chunks(self):
        buffer = RollingAudioBuffer(max_chunks=8)
        for value in range(12):
            buffer.push(np.array([value], dtype=np.int16))

        recent = buffer.drain_recent(3)

        self.assertEqual([int(item[0]) for item in recent], [9, 10, 11])

if __name__ == "__main__":
    unittest.main()
