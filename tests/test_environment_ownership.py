"""Ownership errors must stop processing, not contaminate a neighbouring island."""
import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from ownership import rasterize, dilate


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.raw=np.zeros((16,16,4),np.float32)
        self.raw[2:6,2:6]=[2,.5,.1,1]
        self.raw[10:14,10:14]=[.1,3,.2,1]
        self.charts=[{'id':1,'object':'A','triangles':[[[0,1],[.5,1],[.5,.5]],[[0,1],[.5,.5],[0,.5]]]},
                     {'id':2,'object':'B','triangles':[[[.5,.5],[1,.5],[1,0]],[[.5,.5],[1,0],[.5,0]]]}]

    def test_distinct_ownership_and_unchanged_interiors(self):
        labels,report=rasterize(self.raw,self.charts)
        self.assertEqual(report['owned_pixels'],32)
        self.assertTrue((labels[2:6,2:6]==1).all())
        self.assertTrue((labels[10:14,10:14]==2).all())
        result=dilate(self.raw,labels)
        np.testing.assert_array_equal(result[labels>0],self.raw[labels>0])
        self.assertTrue((result[:,:,3]==1).all())

    def test_overlap_fails(self):
        self.charts.append(dict(self.charts[0],id=3))
        with self.assertRaisesRegex(ValueError,'conflicts='):rasterize(self.raw,self.charts)

    def test_unassigned_pixels_fail(self):
        with self.assertRaisesRegex(ValueError,'unassigned baked pixels=16'):rasterize(self.raw,self.charts[:1])

    def test_fractional_alpha_fails(self):
        self.raw[0,0,3]=.5
        with self.assertRaisesRegex(ValueError,'binary'):rasterize(self.raw,self.charts)


if __name__=='__main__':unittest.main()
