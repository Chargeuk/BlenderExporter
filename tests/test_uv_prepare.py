"""Focused pure contract regressions; Blender integration is a separate gate."""
import copy
import importlib.util
from pathlib import Path
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / 'src/babylon_js/environment_tools/uv_prepare.py'
spec = importlib.util.spec_from_file_location('uv_prepare', HELPER)
uv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uv)

def recipe():
    return {'schema':uv.SCHEMA,'uv1':'UVMap','uv2':'SimpleBake2','selector':{'role':'RECEIVER','all_role':True},
            'default':{'projection':'SMART_PROJECT','angle_degrees':66,'density_weight':1},'overrides':{},
            'chart_overrides':[], 'pack':{'pixel_reference':2048,'gap_pixels':4,'border_pixels':2,
            'precision':5000,'search_seconds':10,'rotation_step_degrees':90}}

class UVPrepareContractTests(unittest.TestCase):
    def test_role_exclusion_and_exact_override(self):
        r=recipe();r['overrides']['Floor']={'projection':'WORLD_XY','density_weight':1.5}
        _, rules=uv.validate_recipe(r,{'Floor':'RECEIVER','SOURCE_Floor':'RETAINED_SOURCE_EXCLUDE_RUNTIME','nav':'NAVIGATION_HELPER'})
        self.assertEqual(set(rules),{'Floor'});self.assertEqual(rules['Floor']['density_weight'],1.5)

    def test_typo_is_not_silently_ignored(self):
        r=recipe();r['overrides']['Foor']={'density_weight':2}
        with self.assertRaises(ValueError):uv.validate_recipe(r,{'Floor':'RECEIVER'})

    def test_ambiguous_and_duplicate_selector_rejected(self):
        for selector in [{'role':'RECEIVER','all_role':True,'names':['Floor']},{'role':'RECEIVER','names':['Floor','Floor']}]:
            r=recipe();r['selector']=selector
            with self.assertRaises(ValueError):uv.validate_recipe(r,{'Floor':'RECEIVER'})

    def test_names_cannot_include_excluded_source(self):
        r=recipe();r['selector']={'role':'RECEIVER','names':['Source']}
        with self.assertRaises(ValueError):uv.validate_recipe(r,{'Source':'RETAINED_SOURCE_EXCLUDE_RUNTIME'})

    def test_invalid_weights_rejected(self):
        for weight in [0,-1,float('nan'),float('inf'),True,'2']:
            r=recipe();r['default']['density_weight']=weight
            with self.subTest(weight=weight),self.assertRaises(ValueError):uv.validate_recipe(r,{'Floor':'RECEIVER'})

    def test_linear_density_has_squared_area(self):
        a=uv.density_scale(12,3,1);b=uv.density_scale(12,3,2.5)
        self.assertAlmostEqual(b/a,2.5);self.assertAlmostEqual((3*b*b)/(3*a*a),6.25)

    def test_density_rejects_degenerate_area(self):
        for args in [(0,1,1),(1,0,1),(1,-1,1),(1,1,float('nan'))]:
            with self.assertRaises(ValueError):uv.density_scale(*args)

    def test_completion_requires_both_finished_and_zero_engine(self):
        self.assertEqual(uv.pack_status({'FINISHED','PASS_THROUGH'},0),'SUCCESS')
        self.assertEqual(uv.pack_status({'FINISHED'},1),'FAILED')
        self.assertEqual(uv.pack_status({'CANCELLED','FINISHED'},0),'FAILED')
        self.assertEqual(uv.pack_status({'RUNNING_MODAL','FINISHED'},0),'PENDING')
        self.assertEqual(uv.pack_status({'PASS_THROUGH'},0),'FAILED')

    def test_wrong_default_1024_or_unbounded_effort_rejected(self):
        for key,value in [('pixel_reference',1024),('search_seconds',0),('search_seconds',61),('precision',500),('gap_pixels',3)]:
            r=recipe();r['pack'][key]=value
            with self.assertRaises(ValueError):uv.validate_recipe(r,{'Floor':'RECEIVER'})

    def test_chart_override_canonical_identity_and_duplicates(self):
        r=recipe();r['chart_overrides']=[{'object':'Shade','polygon_root':6,'density_weight':2}]
        uv.validate_recipe(r,{'Shade':'RECEIVER'})
        r['chart_overrides']*=2
        with self.assertRaises(ValueError):uv.validate_recipe(r,{'Shade':'RECEIVER'})

    def test_missing_chart_rejected_after_unwrap(self):
        obj=SimpleNamespace(name='Shade',data=SimpleNamespace(uv_layers={'SimpleBake2':object()}))
        r=recipe();r['chart_overrides']=[{'object':'Shade','polygon_root':7,'density_weight':2}]
        with patch.object(uv,'_charts',return_value={6:[6,8]}),self.assertRaises(ValueError):
            uv._chart_weights(r,{'Shade':{'density_weight':1}},[obj])

    def test_chart_override_does_not_resize_other_chart(self):
        obj=SimpleNamespace(name='Shade',data=SimpleNamespace(uv_layers={'SimpleBake2':object()}))
        r=recipe();r['chart_overrides']=[{'object':'Shade','polygon_root':6,'density_weight':2}]
        with patch.object(uv,'_charts',return_value={0:[0,1],6:[6,8]}):
            weights=uv._chart_weights(r,{'Shade':{'density_weight':1}},[obj])
        self.assertEqual(weights,{('Shade',0):1,('Shade',6):2})

    def test_recipe_is_not_mutated(self):
        r=recipe();old=copy.deepcopy(r);uv.validate_recipe(r,{'Floor':'RECEIVER'});self.assertEqual(old,r)

    def test_unknown_field_channel_and_seam_typos_rejected(self):
        for key,value in [('uv2','SimpleBake'),('unknown',True)]:
            r=recipe();r[key]=value
            with self.assertRaises(ValueError):uv.validate_recipe(r,{'Floor':'RECEIVER'})
        r=recipe();r['default']['seam_edges']=[1,1]
        with self.assertRaises(ValueError):uv.validate_recipe(r,{'Floor':'RECEIVER'})

class BoolAttributes:
    def __init__(self, counts):
        self.counts=counts;self.items={}
    def get(self,name):return self.items.get(name)
    def new(self,name,type,domain):
        value=SimpleNamespace(name=name,data_type=type,domain=domain,
            data=[SimpleNamespace(value=False) for _ in range(self.counts[domain])])
        self.items[name]=value;return value
    def remove(self,attribute):del self.items[attribute.name]

class FaceList(list):
    def ensure_lookup_table(self):pass
    def index_update(self):
        for i,f in enumerate(self):f.index=i

class ModernUVSelectionTests(unittest.TestCase):
    def mesh(self):return SimpleNamespace(attributes=BoolAttributes({'POINT':4,'EDGE':5,'CORNER':6,'FACE':2}))
    def test_absence_is_distinct_from_all_false_buffer(self):
        m=self.mesh();names={'.uv_select_vert':'CORNER','.uv_select_face':'FACE'}
        old=uv._bool_storage_read(m,names)
        self.assertEqual(old,{'.uv_select_vert':None,'.uv_select_face':None})
        uv._bool_storage_write(m,names,{'.uv_select_vert':[False]*6,'.uv_select_face':[False]*2})
        self.assertIsNotNone(m.attributes.get('.uv_select_vert'))
        uv._bool_storage_write(m,names,old)
        self.assertIsNone(m.attributes.get('.uv_select_vert'));self.assertIsNone(m.attributes.get('.uv_select_face'))

    def test_absent_hide_attributes_restore_absence_after_false_buffers(self):
        m=self.mesh();names=uv._MESH_HIDE_STORAGE
        self.assertEqual(names,{'.hide_vert':'POINT','.hide_edge':'EDGE','.hide_poly':'FACE'})
        before=uv._bool_storage_read(m,names)
        self.assertEqual(before,{name:None for name in names})
        temporary={name:[False]*m.attributes.counts[domain] for name,domain in names.items()}
        uv._bool_storage_write(m,names,temporary)
        self.assertEqual(uv._bool_storage_read(m,names),temporary)
        uv._bool_storage_write(m,names,before)
        self.assertEqual(uv._bool_storage_read(m,names),before)
        for name in names:self.assertIsNone(m.attributes.get(name))

    def test_mixed_hide_presence_and_buffers_restore_independently(self):
        names=uv._MESH_HIDE_STORAGE
        original={'.hide_vert':None,'.hide_edge':[False]*5,'.hide_poly':[True,False]}
        # Separate placement meshes can inherit the same snapshot while keeping
        # excluded-source storage untouched, as after receiver data splitting.
        donor=self.mesh();receiver=self.mesh()
        for mesh in (donor,receiver):uv._bool_storage_write(mesh,names,original)
        donor_before=uv._bool_storage_read(donor,names)
        changed={name:[True]*receiver.attributes.counts[domain] for name,domain in names.items()}
        uv._bool_storage_write(receiver,names,changed)
        uv._bool_storage_write(receiver,names,original)
        self.assertEqual(uv._bool_storage_read(receiver,names),original)
        self.assertEqual(uv._bool_storage_read(donor,names),donor_before)
        self.assertIsNone(receiver.attributes.get('.hide_vert'))
        self.assertIsNotNone(receiver.attributes.get('.hide_edge'))

    def test_modern_mesh_wide_flags_and_layer_specific_pins(self):
        m=self.mesh();names=dict(uv._UV_SELECT_STORAGE,**uv._MESH_SELECT_STORAGE)
        names.update({'.pn.UVMap':'CORNER','.pn.AuxUV':'CORNER'})
        values={name:[i%2==0 for i in range(m.attributes.counts[domain])] for name,domain in names.items()}
        values['.pn.AuxUV']=[not v for v in values['.pn.UVMap']]
        uv._bool_storage_write(m,names,values)
        self.assertEqual(uv._bool_storage_read(m,names),values)
        self.assertNotEqual(uv._bool_storage_read(m,names)['.pn.UVMap'],uv._bool_storage_read(m,names)['.pn.AuxUV'])

    def test_wrong_domain_or_type_is_not_silently_replaced(self):
        for typ,domain in [('BOOLEAN','FACE'),('INT','CORNER')]:
            m=self.mesh();m.attributes.new('.uv_select_vert',typ,domain)
            with self.assertRaises(ValueError):uv._bool_storage_read(m,{'.uv_select_vert':'CORNER'})
            with self.assertRaises(ValueError):uv._bool_storage_write(m,{'.uv_select_vert':'CORNER'},{'.uv_select_vert':[True]*6})

    def test_length_mismatch_fails(self):
        m=self.mesh()
        with self.assertRaises(ValueError):uv._bool_storage_write(m,{'.uv_select_vert':'CORNER'},{'.uv_select_vert':[True]})

    def test_snapshot_identity_mismatch_fails(self):
        m=self.mesh()
        with self.assertRaises(ValueError):uv._bool_storage_write(m,{'.uv_select_vert':'CORNER'},{'.uv_select_edge':[False]*6})

    def bm(self,valid):
        return SimpleNamespace(uv_select_sync_valid=valid,faces=FaceList([
            SimpleNamespace(index=0,uv_select=True,loops=[SimpleNamespace(uv_select_vert=i%2==0,uv_select_edge=i==1) for i in range(3)]),
            SimpleNamespace(index=1,uv_select=False,loops=[SimpleNamespace(uv_select_vert=i==1,uv_select_edge=i!=1) for i in range(3)])]))

    def test_edit_vertex_edge_face_flags_and_cache_validity_restore(self):
        for valid in [False,True]:
            bm=self.bm(valid);old=uv._edit_uv_snapshot(bm)
            bm.uv_select_sync_valid=not valid
            for f in bm.faces:
                f.uv_select=not f.uv_select
                for loop in f.loops:loop.uv_select_vert=loop.uv_select_edge=False
            uv._restore_edit_uv(bm,old)
            self.assertEqual(uv._edit_uv_snapshot(bm),old)

    def test_changed_edit_topology_is_explicit_failure(self):
        bm=self.bm(False);old=uv._edit_uv_snapshot(bm);bm.faces[0].loops.pop()
        with self.assertRaises(ValueError):uv._restore_edit_uv(bm,old)

geometry_spec=importlib.util.spec_from_file_location('uv_geometry',HELPER.parent/'uv_geometry.py')
geometry=importlib.util.module_from_spec(geometry_spec)
geometry_spec.loader.exec_module(geometry)

class UnpackedChartNormalizationTests(unittest.TestCase):
    def entry(self,triangles):return {'id':4,'object':'OriginalShade','polygon_root':17,'triangles':triangles}
    def test_negative_out_of_tile_chart_preserves_original_and_aspect(self):
        entry=self.entry([[[-8,5],[-4,5],[-8,7]]]);old=copy.deepcopy(entry)
        normalized,transform=uv.normalize_chart_for_audit(entry)
        self.assertEqual(entry,old)
        self.assertEqual(normalized['triangles'],[[[0,0],[1,0],[0,.5]]])
        self.assertEqual(transform['original_entry_identity'],{'id':4,'object':'OriginalShade','polygon_root':17})
        self.assertEqual(transform['translation_original_uv'],[8,-5])
        self.assertEqual(transform['uniform_divisor_original_uv'],4)
        self.assertAlmostEqual(transform['normalized_area_epsilon_uv_squared']/(1e-12/16),1,places=12)
        self.assertTrue(uv._audit_unpacked_chart(entry,geometry.audit_catalogue)['passed'])

    def test_uniform_scale_translation_invariance_and_epsilon_units(self):
        entry=self.entry([[[0,0],[4,0],[0,2]]])
        moved=self.entry([[[10+3*p[0],-9+3*p[1]] for p in t] for t in entry['triangles']])
        a,ta=uv.normalize_chart_for_audit(entry);b,tb=uv.normalize_chart_for_audit(moved)
        self.assertEqual(a['triangles'],b['triangles'])
        self.assertAlmostEqual(ta['normalized_area_epsilon_uv_squared']/tb['normalized_area_epsilon_uv_squared'],9)
        for e in [entry,moved]:self.assertTrue(uv._audit_unpacked_chart(e,geometry.audit_catalogue)['passed'])

    def test_clockwise_winding_is_not_flipped(self):
        entry=self.entry([[[-2,-2],[-2,1],[4,-2]]])
        normalized,_=uv.normalize_chart_for_audit(entry)
        def signed(t):
            a,b,c=t;return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        self.assertLess(signed(entry['triangles'][0]),0)
        self.assertLess(signed(normalized['triangles'][0]),0)

    def test_overlap_preserved_with_original_area_interpretation(self):
        triangle=[[-30,40],[-22,40],[-30,48]]
        report=uv._audit_unpacked_chart(self.entry([triangle,copy.deepcopy(triangle)]),geometry.audit_catalogue)
        self.assertFalse(report['passed']);self.assertEqual(report['counts']['within_island_overlap_pairs'],1)
        span=report['unpacked_chart_normalization']['uniform_divisor_original_uv']
        self.assertAlmostEqual(report['overlaps'][0]['area']*span*span,32)

    def test_degenerate_original_area_threshold_preserved(self):
        entry=self.entry([[[0,0],[1e-6,0],[0,1e-6]],[[1000000,0],[999999,0],[1000000,1]]])
        report=uv._audit_unpacked_chart(entry,geometry.audit_catalogue)
        self.assertFalse(report['passed']);self.assertEqual(report['counts']['degenerate_triangles'],1)
        self.assertAlmostEqual(report['tolerances']['area_epsilon']/1e-24,1,places=12)
        self.assertEqual(report['unpacked_chart_normalization']['original_area_epsilon_weighted_uv_squared'],1e-12)

    def test_tiny_normalized_triangle_above_original_threshold_is_not_dropped(self):
        entry=self.entry([[[0,0],[4e-6,0],[0,4e-6]],[[1000000,0],[999999,0],[1000000,1]]])
        report=uv._audit_unpacked_chart(entry,geometry.audit_catalogue)
        self.assertTrue(report['passed']);self.assertEqual(report['counts']['degenerate_triangles'],0)

    def test_shared_edge_is_legal_after_normalization(self):
        entry=self.entry([[[-8,2],[-4,2],[-8,6]],[[-4,2],[-4,6],[-8,6]]])
        report=uv._audit_unpacked_chart(entry,geometry.audit_catalogue)
        self.assertTrue(report['passed']);self.assertEqual(report['counts']['overlap_pairs'],0)

    def test_zero_or_nonfinite_span_and_coordinates_rejected(self):
        cases=[[[[3,3],[3,3],[3,3]]],[[[0,0],[float('nan'),1],[1,1]]],
               [[[0,0],[float('inf'),1],[1,1]]],[[[-1e308,0],[1e308,0],[0,1]]]]
        for triangles in cases:
            with self.subTest(triangles=triangles),self.assertRaises(ValueError):uv.normalize_chart_for_audit(self.entry(triangles))

class UVPMGroupRestorationTests(unittest.TestCase):
    def test_norm_iparam_restored_without_enabled_policy_change(self):
        def descriptor(name,enabled):
            fields={'iparam_name':SimpleNamespace(is_readonly=False,type='STRING'),
                    'enable':SimpleNamespace(is_readonly=False,type='BOOLEAN')}
            return SimpleNamespace(iparam_name=name,enable=enabled,bl_rna=SimpleNamespace(properties=fields))
        groups=SimpleNamespace(**{key:descriptor('original_'+key,True) for key in
                                  ('lock_group','track_group','stack_group','norm_group')})
        props=SimpleNamespace(numbered_groups_descriptors=groups);restore=[]
        uv._capture_uvpm_group_settings(props,restore)
        self.assertTrue(groups.norm_group.enable)
        self.assertFalse(any(owner is groups.norm_group and name=='enable' for owner,name,_ in restore))
        # Simulate UVPM pre_operation overwriting descriptor names.
        for key in ('lock_group','track_group','norm_group'):getattr(groups,key).iparam_name='engine_'+key
        for owner,name,value in reversed(restore):setattr(owner,name,value)
        self.assertEqual(groups.norm_group.iparam_name,'original_norm_group')
        for key in ('lock_group','track_group','stack_group','norm_group'):
            self.assertEqual(getattr(groups,key).iparam_name,'original_'+key)
            self.assertTrue(getattr(groups,key).enable)

class EditUVGuardTests(unittest.TestCase):
    class Loop:
        def __init__(self,vertex,value):self.vert=SimpleNamespace(index=vertex);self.value=SimpleNamespace(uv=value)
        def __getitem__(self,layer):return self.value

    def fixture(self):
        vertices=[SimpleNamespace(co=p) for p in [(0,0,0),(1,0,0),(1,1,0),(0,1,0)]]
        polygons=[SimpleNamespace(index=0,vertices=[0,1,2],loop_indices=[0,1,2],use_smooth=False,material_index=0),
                  SimpleNamespace(index=1,vertices=[0,2,3],loop_indices=[3,4,5],use_smooth=False,material_index=0)]
        values=[(0,0),(1,0),(1,1),(.1,.2),(.9,.8),(0,1)]
        corners=[0,1,2,0,2,3]
        layer=SimpleNamespace(data=[SimpleNamespace(uv=p) for p in values])
        mesh=SimpleNamespace(is_editmode=False,vertices=vertices,edges=[SimpleNamespace(vertices=e,use_seam=False) for e in [(0,1),(1,2),(2,0),(2,3),(3,0)]],
            polygons=polygons,loops=[SimpleNamespace(vertex_index=i) for i in corners],uv_layers={'UVMap':layer},
            corner_normals=[SimpleNamespace(vector=(0,0,1)) for _ in values],materials=[])
        faces=[SimpleNamespace(index=p.index,loops=[self.Loop(v,values[l]) for v,l in zip(p.vertices,p.loop_indices)]) for p in polygons]
        bm=SimpleNamespace(verts=[object() for _ in vertices],edges=[object() for _ in mesh.edges],faces=faces,
            loops=SimpleNamespace(layers=SimpleNamespace(uv={'UVMap':'verified-layer'})))
        return mesh,bm,values

    def test_extant_uv_read_in_canonical_polygon_corner_order(self):
        mesh,bm,values=self.fixture();bm.faces.reverse()
        bm.faces[1].loops=bm.faces[1].loops[1:]+bm.faces[1].loops[:1]
        self.assertEqual(uv._edit_uv_values(mesh,bm),values)

    def test_object_and_edit_guards_match_but_true_uv_change_is_detected(self):
        mesh,bm,values=self.fixture()
        obj=SimpleNamespace(data=mesh,matrix_world=[(1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)],parent=None,get=lambda key:'RECEIVER')
        before=uv._guard(obj);mesh.is_editmode=True;mesh.uv_layers['UVMap'].data=[]
        with patch.dict('sys.modules',{'bmesh':SimpleNamespace(from_edit_mesh=lambda m:bm)}):
            self.assertEqual(uv._guard(obj),before)
            bm.faces[0].loops[0].value.uv=(.25,.25)
            self.assertNotEqual(uv._guard(obj),before)

    def test_missing_edit_layer_is_explicit_failure(self):
        mesh,bm,_=self.fixture();bm.loops.layers.uv={}
        with self.assertRaises(ValueError):uv._edit_uv_values(mesh,bm)

    def test_reversed_corner_order_is_not_silently_accepted(self):
        mesh,bm,_=self.fixture();bm.faces[0].loops.reverse()
        with self.assertRaises(ValueError):uv._edit_uv_values(mesh,bm)

    def test_changed_topology_or_ambiguous_polygon_indices_fail(self):
        mesh,bm,_=self.fixture();bm.verts.pop()
        with self.assertRaises(ValueError):uv._edit_uv_values(mesh,bm)
        mesh,bm,_=self.fixture();bm.faces[1].index=0
        with self.assertRaises(ValueError):uv._edit_uv_values(mesh,bm)

    def test_missing_or_duplicate_canonical_corner_fails(self):
        mesh,bm,_=self.fixture();mesh.polygons[1].loop_indices=[0,4,5]
        with self.assertRaises(ValueError):uv._edit_uv_values(mesh,bm)

if __name__=='__main__':unittest.main()
