"""Recombination must preserve bake ownership and never require a stale combined map."""
import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from reuse import reuse_plan,validate_ownership,validate_catalogue,load_labels
from ownership import rasterize


class ReuseTests(unittest.TestCase):
    def test_compressed_ownership_is_lossless(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            labels=np.array([[0,2],[9,0]],np.int32);p=Path(directory)/'labels.npz'
            np.savez_compressed(p,labels=labels)
            np.testing.assert_array_equal(load_labels(p),labels)

    def test_combine_uses_only_three_masters(self):
        combining,names=reuse_plan('combine',True,False,'labels','catalogue')
        self.assertTrue(combining)
        self.assertEqual(names,('direct','indirect','island_ids'))

    def test_capture_reuse_is_not_implicit_recombination(self):
        combining,names=reuse_plan('capture',True,False,None,None)
        self.assertFalse(combining)
        self.assertIn('combined',names)

    def test_capture_can_explicitly_recombine(self):
        self.assertTrue(reuse_plan('capture',True,True,'labels','catalogue')[0])

    def test_recombine_requires_original_ownership(self):
        with self.assertRaisesRegex(ValueError,'requires --ownership'):
            reuse_plan('combine',True,False,None,None)
        with self.assertRaisesRegex(ValueError,'requires --reuse'):
            reuse_plan('capture',False,True,'labels','catalogue')

    def test_rebake_cannot_claim_reuse(self):
        for command in ('bake','ownership','process'):
            with self.assertRaises(ValueError):reuse_plan(command,True,False,None,None)

    def test_current_uvs_and_labels_must_agree(self):
        raw=np.zeros((16,16,4),np.float32);raw[2:6,2:6,3]=1
        charts=[{'id':1,'object':'A','triangles':[[[0,1],[.5,1],[.5,.5]],[[0,1],[.5,.5],[0,.5]]]}]
        labels,_=rasterize(raw,charts)
        validate_ownership(labels,charts,charts,16)
        with self.assertRaisesRegex(ValueError,'catalogue differs'):
            validate_ownership(labels,charts,[dict(charts[0],object='different')],16)
        wrong=labels.copy();wrong[labels>0]=2
        with self.assertRaisesRegex(ValueError,'unknown island'):
            validate_ownership(wrong,charts,charts,16)
        with self.assertRaisesRegex(ValueError,'integer image'):
            validate_ownership(labels.astype(float),charts,charts,16)

    def test_saved_ids_survive_different_enumeration(self):
        a={'id':9,'object':'A','polygon_root':0,'triangles':[[[0,0],[1,0],[1,1]]]}
        b={'id':4,'object':'B','polygon_root':0,'triangles':[[[0,0],[1,1],[0,1]]]}
        validate_catalogue([a,b],[dict(b,id=1),dict(a,id=2)])
        with self.assertRaisesRegex(ValueError,'catalogue differs'):
            validate_catalogue([a,b],[dict(a,triangles=b['triangles']),b])
        with self.assertRaisesRegex(ValueError,'duplicate'):
            validate_catalogue([a,a],[a,b])

if __name__=='__main__':unittest.main()
