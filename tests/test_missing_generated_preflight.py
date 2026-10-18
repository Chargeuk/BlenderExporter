"""Fresh dimension placeholders are temporary, explicit, and generated-only."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HELPER=Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools/image_binding.py'


class Image(dict):
    def __init__(self,name,path='',source='FILE',size=(0,0)):
        super().__init__();self.name=name;self.filepath=path;self.source=source;self.size=size
        self.packed_file=None;self.alpha_mode='PREMUL';self.colorspace_settings=types.SimpleNamespace(name='Non-Color');self.remaps=[]
    def user_remap(self,image):self.remaps.append(image)

class Images(list):
    def new(self,name,width,height,**kwargs):
        image=Image(name,source='GENERATED',size=(width,height));self.append(image);return image
    def remove(self,image):
        for i,item in enumerate(self):
            if item is image:self.pop(i);return
        raise AssertionError('Unknown image')


class FreshPreflightGuards(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup);self.root=Path(self.folder.name)
        self.images=Images([Image('direct',str(self.root/'direct.exr')),Image('authored',str(self.root/'colour.png'))])
        self.original=self.images[0]
        self.bpy=types.SimpleNamespace(app=types.SimpleNamespace(background=True),data=types.SimpleNamespace(images=self.images),path=types.SimpleNamespace(abspath=lambda x:x))
        with patch.dict(sys.modules,{'bpy':self.bpy}):
            spec=importlib.util.spec_from_file_location('tested_missing_generated',HELPER)
            self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.config=dict(_root=str(self.root),size=32,images=dict(direct='direct.exr',indirect='indirect.exr',island_ids='ids.exr'),source_images=['colour.png'])

    def test_default_does_not_substitute(self):
        with self.module.missing_generated_preflight(self.config) as rows:self.assertEqual(rows,[])
        self.assertEqual(self.original.remaps,[])

    def test_missing_generated_gets_dimensions_then_restores(self):
        with self.module.missing_generated_preflight(self.config,True) as rows:
            self.assertEqual(len(rows),1);self.assertTrue(rows[0]['nonPromotable'])
            temporary=self.original.remaps[-1]
            self.assertEqual(temporary.size,(32,32));self.assertTrue(temporary['bjs_preflight_placeholder'])
            self.assertEqual(self.images[1].remaps,[])
        self.assertEqual(len(self.images),2);self.assertIs(temporary.remaps[-1],self.original)
        self.assertFalse((self.root/'direct.exr').exists())

    def test_existing_wrong_sized_map_is_not_replaced(self):
        (self.root/'direct.exr').write_bytes(b'existing')
        with self.module.missing_generated_preflight(self.config,True) as rows:self.assertEqual(rows,[])
        self.assertEqual(self.original.remaps,[])

    def test_authored_overlap_rejects(self):
        self.config['source_images'].append('direct.exr')
        with self.assertRaises(ValueError):
            with self.module.missing_generated_preflight(self.config,True):pass
        self.assertEqual(self.original.remaps,[])

    def test_exception_restores_all_users(self):
        with self.assertRaises(RuntimeError):
            with self.module.missing_generated_preflight(self.config,True):raise RuntimeError('dimension failure')
        temporary=self.original.remaps[-1]
        self.assertIs(temporary.remaps[-1],self.original);self.assertEqual(len(self.images),2)

    def test_live_scene_refuses_placeholder_creation(self):
        self.bpy.app.background=False
        with self.assertRaises(ValueError):
            with self.module.missing_generated_preflight(self.config,True):pass
        self.assertEqual(self.original.remaps,[])


if __name__=='__main__':unittest.main()
