"""Measured coordinate regressions, without desktop access."""
import unittest
from desktop_coordinates import pointer_calibration, calibrated_point, logical_point, relative_box

class CoordinateTests(unittest.TestCase):
    def samples(self):
        return [{'client': [x,y], 'logical': [-1920+1.1*x, 40+1.1*y]}
                for x,y in [(100,100),(400,300),(220,420)]]

    def test_fractional_scale_and_negative_origin(self):
        p = calibrated_point([300,200], pointer_calibration(self.samples()))
        self.assertAlmostEqual(p[0], -1590)
        self.assertAlmostEqual(p[1], 260)
        self.assertEqual(logical_point([-960,540], {'logical_position':[-1920,0],
                         'logical_size':[1920,1080]}), {'x':.5,'y':.5})

    def test_independent_probe_disagreement_refused(self):
        samples = self.samples()
        samples[2]['logical'][0] += 10
        with self.assertRaises(ValueError): pointer_calibration(samples)

    def test_invalid_samples_refused(self):
        for value in [float('nan'), float('inf'), True]:
            with self.subTest(value=value):
                samples = self.samples()
                samples[0]['client'][0] = value
                with self.assertRaises(ValueError): pointer_calibration(samples)

    def test_outside_monitor_and_incompatible_frames_refused(self):
        with self.assertRaises(ValueError):
            logical_point([1920,20], {'logical_position':[0,0], 'logical_size':[1920,1080]})
        with self.assertRaises(ValueError): relative_box([1,1,20,20],[0,0,100,100],[0,0,200,100])
