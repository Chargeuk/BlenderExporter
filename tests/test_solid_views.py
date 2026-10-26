"""Neutral solid diagnostics membership/camera/face safety, without Blender."""
import copy,importlib.util,sys,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('solid_views_contract',HERE/'solid_views.py')
solid=importlib.util.module_from_spec(spec);spec.loader.exec_module(solid)

def recipe():
    return dict(schema_version=1,source_filename='test.blend',source_sha256='a'*64,scene='Scene',
      delivery_names=['wall','frame'],expected_delivery_meshes=2,expected_delivery_triangles=14,dimensions=[800,600],
      views=[dict(id='complete_plusX',matrix_world=[[1,0,0,8],[0,1,0,0],[0,0,1,2],[0,0,0,1]],lens=50,
                  camera_properties=dict(type='ORTHO',ortho_scale=10),full_delivery=True,backface_culling=False)])

class SolidConfigTests(unittest.TestCase):
    def test_complete_membership_and_explicit_omission_subset(self):
        c=recipe();self.assertIs(solid.validate_config(c),c)
        c['views'].append(dict(id='frame_cap',include_names=['frame'],omit_faces={'frame':[0,2]},camera='inside',backface_culling=True))
        self.assertIs(solid.validate_config(c),c)
    def test_rejects_fake_complete_object_or_face_coverage(self):
        for change in (dict(include_names=['wall']),dict(omit_faces={'frame':[0]})):
            c=recipe();c['views'][0].update(change)
            with self.assertRaisesRegex(ValueError,'Full delivery'):solid.validate_config(c)
    def test_rejects_unknown_duplicate_or_empty_membership(self):
        for names in ([],['wall','wall'],['unknown'],['wall',[]]):
            c=recipe();c['views'][0]['include_names']=names
            with self.assertRaises(ValueError):solid.validate_config(c)
    def test_rejects_invalid_omission_ids_and_omitted_object(self):
        for ids in ([-1],[True],[.5],[1,1],[[]]):
            c=recipe();c['views'][0].update(full_delivery=False,omit_faces={'frame':ids})
            with self.assertRaises(ValueError):solid.validate_config(c)
        c=recipe();c['views'][0].update(full_delivery=False,include_names=['wall'],omit_faces={'frame':[0]})
        with self.assertRaisesRegex(ValueError,'absent'):solid.validate_config(c)
    def test_source_count_path_hash_and_flags_are_explicit(self):
        changes=[('source_filename','../test.blend'),('source_filename','H:\\test.blend'),('source_sha256','A'*64),
                 ('expected_delivery_triangles',True),('expected_delivery_meshes',1),('delivery_names',['wall','wall'])]
        for key,value in changes:
            c=recipe();c[key]=value
            with self.assertRaises(ValueError):solid.validate_config(c)
        c=recipe();c['views'][0]['backface_culling']='false'
        with self.assertRaises(ValueError):solid.validate_config(c)
    def test_reuses_camera_matrix_id_and_property_guards(self):
        variants=[dict(id='../escape'),dict(matrix_world=[[0]*4]*4),dict(lens=0),dict(camera_properties={'ortho_scale':0}),dict(camera_properties={'unknown':2})]
        for change in variants:
            c=recipe();c['views'][0].update(change)
            with self.assertRaises(ValueError):solid.validate_config(c)
    def test_existing_camera_mode_contract_unchanged(self):
        import camera_views
        for mode in ('physical','baked'):
            c={'scene':'Scene','capture':{'views':{'dimensions':[64,64],'samples':1,'camera_set':[{'id':'old','camera':'saved','mode':mode}]}}}
            self.assertEqual(camera_views.validate_camera_set(c)[0]['mode'],mode)
        c['capture']['views']['camera_set'][0]['mode']='solid'
        with self.assertRaises(ValueError):camera_views.validate_camera_set(c)

if __name__=='__main__':unittest.main()
