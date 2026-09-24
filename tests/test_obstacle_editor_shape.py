"""Editor geometry and navigation behavior without launching Tk."""
import copy
import unittest
from types import SimpleNamespace

from level_visualizer import ObstacleEditorWindow, compile_obstacle_card
from obstacle_library import GROUPS, apply_geometry_overrides, as_level, build_variant, load_catalog, place, vertex
from terrain_export import export_level, surface_count
from world_scale import scale_world_data


class ShapeChoice:
    def configure(self, **_kwargs):
        pass

    def current(self, index=None):
        if index is not None:
            self.index = index
        return self.index


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class EditorShapeTests(unittest.TestCase):
    def test_add_square_survives_source_scale_and_keeps_existing_coins(self):
        family = load_catalog()['flat']
        variant = copy.deepcopy(family['variants'][0])
        original = build_variant(family, variant, scale=2)
        editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        editor.module = copy.deepcopy(original)
        editor.shapes = [(group, shape) for group in GROUPS for shape in editor.module[group]
                         if 'points' in shape]
        editor.shape_index = 0
        editor.selected = None
        editor.added_shapes = set()
        editor.converted_shapes = set()
        editor.changed_shapes = set()
        editor.dirty = False
        editor.shape_choice = ShapeChoice()
        editor.shape_mode = Value('MainPlatform')
        editor.fit = lambda: None
        editor.select = lambda _index: None
        editor.add_shape()
        self.assertTrue(editor.dirty)
        self.assertEqual(editor.shape_choice.index, editor.shape_index)
        shape = editor.shapes[-1][1]
        self.assertEqual((min(p['x'] for p in shape['points']), min(p['y'] for p in shape['points']),
                          max(p['x'] for p in shape['points']), max(p['y'] for p in shape['points'])),
                         (0, -7, 3, 8))
        variant['addedShapes'] = {'MainPlatform': [scale_world_data(shape, .5)]}
        rebuilt = build_variant(family, variant, scale=2)
        self.assertEqual(rebuilt['MainPlatform'][-1]['points'], shape['points'])
        self.assertEqual([o for o in original['InteractableObject'] if o['type']=='coin'],
                         [o for o in rebuilt['InteractableObject'] if o['type']=='coin'])
        compile_obstacle_card(family, variant)

    def test_ground_and_deadzone_modes_create_four_point_shapes(self):
        family = load_catalog()['flat']
        variant = copy.deepcopy(family['variants'][0])
        editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        editor.module = build_variant(family, variant, scale=2)
        editor.shapes = [(group, shape) for group in GROUPS for shape in editor.module[group]
                         if 'points' in shape]
        editor.shape_index = 0
        editor.selected = None
        editor.added_shapes = set()
        editor.converted_shapes = set()
        editor.changed_shapes = set()
        editor.shape_choice = ShapeChoice()
        editor.shape_mode = Value('MainPlatform')
        editor.status = SimpleNamespace(set=lambda _message: None)
        editor.fit = lambda: None
        editor.select = lambda _index: None
        self.assertEqual(len(editor.current()[2]), len(editor.shapes[0][1]['points']))
        editor.add_shape()
        ground_shape = editor.shapes[-1][1]
        self.assertEqual(len(ground_shape['points']), 4)
        editor.convert_selected_shape('deadzone')
        self.assertEqual(editor.shapes[-1][0], 'deadzone')
        editor.add_shape()
        name, zone = editor.shapes[-1]
        self.assertEqual(name, 'deadzone')
        self.assertEqual(len(zone['points']), 4)
        self.assertEqual([(p['x'], p['y']) for p in zone['points']],
                         [(0, 8), (3, 8), (3, 5), (0, 5)])
        additions = {'deadzone': [scale_world_data(shape, .5) for group, shape in editor.shapes
                                  if (group, shape['id']) in editor.added_shapes]}
        variant['addedShapes'] = additions
        rebuilt = build_variant(family, variant, scale=2)
        self.assertEqual([shape['id'] for shape in rebuilt['deadzone'][-2:]],
                         [shape['id'] for shape in editor.module['deadzone'][-2:]])
        compile_obstacle_card(family, variant)

    def test_zxc_convert_selected_shape_without_changing_selection(self):
        family = load_catalog()['flat']
        variant = copy.deepcopy(family['variants'][0])
        editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        editor.family = family
        editor.variant = variant
        editor.module = build_variant(family, variant, scale=2)
        editor.shapes = [(group, shape) for group in GROUPS for shape in editor.module[group]
                         if 'points' in shape]
        editor.shape_index = 0
        editor.selected = None
        editor.shape_choice = ShapeChoice()
        editor.shape_mode = Value('MainPlatform')
        editor.status = SimpleNamespace(set=lambda _message: None)
        editor.added_shapes = set()
        editor.converted_shapes = set()
        editor.changed_shapes = set()
        editor.select = lambda _index: None
        shape_id = editor.shapes[0][1]['id']
        self.assertEqual(editor.shape_mode_hotkey(SimpleNamespace(widget=object()), 'RampPlatform'), 'break')
        self.assertEqual((editor.shape_index, editor.shapes[0][0], editor.shapes[0][1]['id']),
                         (0, 'RampPlatform', shape_id))
        editor.convert_selected_shape('deadzone')
        self.assertEqual((editor.shape_index, editor.shapes[0][0], editor.shape_mode.get()),
                         (0, 'deadzone', 'deadzone'))
        proposed = editor.proposed_variant()
        rebuilt = build_variant(family, proposed, scale=2)
        self.assertFalse(any(shape['id'] == shape_id for shape in rebuilt['MainPlatform']))
        self.assertTrue(any(shape['id'] == shape_id for shape in rebuilt['deadzone']))
        compile_obstacle_card(family, proposed)

    def test_deadzone_converts_to_ground_with_two_bottom_corners(self):
        family, variant = next((family, variant)
                               for family in load_catalog().values()
                               for variant in family['variants']
                               if build_variant(family, variant)['deadzone'])
        editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        editor.family = family
        editor.variant = copy.deepcopy(variant)
        editor.module = build_variant(family, variant, scale=2)
        editor.shapes = [(group, shape) for group in GROUPS for shape in editor.module[group]
                         if 'points' in shape]
        editor.shape_index = next(i for i, (group, _shape) in enumerate(editor.shapes)
                                  if group == 'deadzone')
        editor.selected = None
        editor.shape_choice = ShapeChoice()
        editor.shape_mode = Value('deadzone')
        editor.status = SimpleNamespace(set=lambda _message: None)
        editor.added_shapes = set()
        editor.converted_shapes = set()
        editor.changed_shapes = set()
        editor.select = lambda _index: None
        shape_id = editor.current()[1]['id']
        editor.convert_selected_shape('MainPlatform')
        self.assertEqual(editor.current()[0], 'MainPlatform')
        proposed = editor.proposed_variant()
        rebuilt = build_variant(family, proposed, scale=2)
        ground_shape = next(shape for shape in rebuilt['MainPlatform'] if shape['id'] == shape_id)
        self.assertEqual(ground_shape['metadata']['drivingSurfaceCount'], len(ground_shape['points'])-2)
        compile_obstacle_card(family, proposed)

    def test_v_converts_selected_shape_to_free_platform_and_adds_free_shape(self):
        family = load_catalog()['flat']
        variant = copy.deepcopy(family['variants'][0])
        editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        editor.family = family
        editor.variant = variant
        editor.module = build_variant(family, variant, scale=2)
        editor.shapes = [(group, shape) for group in GROUPS for shape in editor.module[group]
                         if 'points' in shape]
        editor.shape_index = 0
        editor.selected = 0
        editor.shape_choice = ShapeChoice()
        editor.shape_mode = Value('MainPlatform')
        editor.status = SimpleNamespace(set=lambda _message: None)
        editor.added_shapes = set()
        editor.converted_shapes = set()
        editor.changed_shapes = set()
        editor.select = lambda _index: None
        editor.fit = lambda: None
        original_id = editor.current()[1]['id']
        self.assertEqual(editor.shape_mode_hotkey(SimpleNamespace(widget=object()), 'FreePlatform'), 'break')
        self.assertEqual((editor.shape_index, editor.current()[0], editor.current()[1]['id']),
                         (0, 'FreePlatform', original_id))
        editor.nudge_point(100, 0)
        self.assertEqual(editor.current()[2][0]['x'], 100)
        bottom_index = editor.current()[1]['metadata']['drivingSurfaceCount']
        editor.selected = bottom_index
        bottom_x = editor.current()[2][bottom_index]['x']
        editor.nudge_point(7, 5)
        self.assertEqual(editor.current()[2][bottom_index]['x'], bottom_x+7)
        count_before = len(editor.current()[2])
        editor.remove_point()
        self.assertEqual(len(editor.current()[2]), count_before-1)
        proposed = editor.proposed_variant()
        rebuilt = build_variant(family, proposed, scale=2)
        self.assertTrue(any(shape['id'] == original_id for shape in rebuilt['FreePlatform']))
        editor.add_shape()
        group, added, _points = editor.current()
        self.assertEqual(group, 'FreePlatform')
        self.assertEqual(len(added['points']), 4)
        self.assertNotIn('drivingSurfaceCount', added.get('metadata', {}))
        proposed = editor.proposed_variant()
        rebuilt = build_variant(family, proposed, scale=2)
        self.assertEqual(next(shape['points'] for shape in rebuilt['FreePlatform']
                              if shape['id'] == added['id']), added['points'])
        scene = compile_obstacle_card(family, proposed)
        self.assertIn(added['id'], {path.source_id for path in scene.paths})

    def test_open_free_platform_preserves_its_two_points(self):
        family = load_catalog()['flat']
        variant = copy.deepcopy(family['variants'][0])
        variant['addedShapes'] = {'FreePlatform': [{
            'id': 'free_line', 'closed': False,
            'points': [vertex(-4, 12, mode='linear'), vertex(5, 15, mode='linear')]}]}
        module = build_variant(family, variant, scale=2)
        shape = module['FreePlatform'][0]
        self.assertFalse(shape['closed'])
        self.assertEqual([(p['x'], p['y']) for p in shape['points']], [(-8, 24), (10, 30)])
        placed = place(module, 20, -5, 'demo')
        level = as_level([placed], 'demo', 'demo', preview=True)
        self.assertEqual([(p['x'], p['y']) for p in level['FreePlatform'][0]['points']],
                         [(12, 19), (30, 25)])
        compile_obstacle_card(family, variant)

    def test_explicit_bottom_corner_override_roundtrip(self):
        family = load_catalog()['flat']
        variant = copy.deepcopy(family['variants'][0])
        module = build_variant(family, variant)
        shape = module['MainPlatform'][0]
        changed = copy.deepcopy(shape['points'])
        changed[-2]['y'] -= 1
        override = {'MainPlatform': {shape['id']: {
            'drivingSurfaceCount': shape['metadata']['drivingSurfaceCount'],
            'points': changed}}}
        apply_geometry_overrides(module, override)
        self.assertEqual(module['MainPlatform'][0]['points'][-2]['y'], changed[-2]['y'])
        variant['geometryOverrides'] = override
        rebuilt = build_variant(family, variant)
        self.assertEqual(rebuilt['MainPlatform'][0]['points'], changed)

    def test_bottom_corners_move_freely_and_survive_save_and_export(self):
        family = load_catalog()['flat']
        variant = copy.deepcopy(family['variants'][0])
        editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        editor.family = family
        editor.variant = variant
        editor.module = build_variant(family, variant, scale=2)
        editor.shapes = [(group, shape) for group in GROUPS for shape in editor.module[group]
                         if 'points' in shape]
        editor.shape_index = 0
        editor.added_shapes = set()
        editor.converted_shapes = set()
        editor.changed_shapes = set()
        editor.dirty = False
        editor.status = SimpleNamespace(set=lambda _message: None)
        editor.select = lambda _index: None
        group, shape, points = editor.current()
        bottom_index = shape['metadata']['drivingSurfaceCount']
        editor.selected = bottom_index
        original_x = points[bottom_index]['x']
        editor.nudge_point(1, 0)
        self.assertEqual(points[bottom_index]['x'], original_x+1)
        editor.drag = ('point', bottom_index)
        editor.world = lambda _x, _y: (12, 3)
        editor.mouse_move(SimpleNamespace(x=0, y=0, state=0))
        self.assertEqual((points[bottom_index]['x'], points[bottom_index]['y']), (12, 3))
        editor.fields = {name: Value(value) for name, value in (
            ('x', -4), ('y', 7), ('in_x', 0), ('in_y', 0), ('out_x', 0), ('out_y', 0))}
        editor.mode = Value('linear')
        editor.apply_fields()
        self.assertEqual((points[bottom_index]['x'], points[bottom_index]['y']), (-4, 7))
        self.assertTrue(shape['metadata']['freeBottomCorners'])
        editor.selected = bottom_index+1
        left_x = points[-1]['x']
        editor.nudge_point(-1, 0)
        self.assertEqual(points[-1]['x'], left_x-1)
        editor.selected = 0
        editor.nudge_point(0, 1)
        self.assertEqual(points[-1]['x'], left_x-1)
        self.assertEqual(points[-2]['x'], -4)
        proposed = editor.proposed_variant()
        rebuilt = build_variant(family, proposed, scale=2)
        saved = next(item for item in rebuilt[group] if item['id'] == shape['id'])
        self.assertEqual((saved['points'][bottom_index]['x'], saved['points'][bottom_index]['y']), (-4, 7))
        self.assertEqual(saved['points'][-1]['x'], left_x-1)
        self.assertEqual(surface_count(saved), bottom_index)
        exported = export_level({'MainPlatform': [saved]}, boundary_padding=20)
        exported_shape = exported['MainPlatform'][0]
        self.assertEqual((exported_shape['points'][-2]['x'], exported_shape['points'][-2]['y']), (-4, 7))
        self.assertEqual(export_level(exported), exported)

    def test_nudge_is_one_unit_and_middle_drag_pans(self):
        editor = ObstacleEditorWindow.__new__(ObstacleEditorWindow)
        point = {'x': 0, 'y': 5}
        shape = {'id': 'road', 'points': [point, {'x': 2, 'y': 5}],
                 'metadata': {'drivingSurfaceCount': 2}}
        editor.current = lambda: ('MainPlatform', shape, shape['points'])
        editor.selected = 0
        editor.dirty = False
        editor.changed_shapes = set()
        editor.status = SimpleNamespace(set=lambda _message: None)
        editor.sync_terrain_closure = lambda _shape: None
        editor.select = lambda _index: None
        editor.nudge_point(0, 1)
        editor.nudge_point(1, 0)
        self.assertEqual((point['x'], point['y']), (1, 6))
        editor.nudge_point(1, 0)
        self.assertEqual(point['x'], 2)
        editor.nudge_point(1, 0)
        self.assertEqual(point['x'], 2)
        editor.canvas = SimpleNamespace(focus_set=lambda: None)
        editor.view_offset = (10, 20)
        editor.pan_anchor = None
        editor.redraw = lambda: None
        editor.pan_start(SimpleNamespace(x=100, y=200))
        editor.pan_move(SimpleNamespace(x=112, y=195))
        self.assertEqual(editor.view_offset, (22, 15))
        editor.pan_end(None)
        self.assertIsNone(editor.pan_anchor)


if __name__ == '__main__':
    unittest.main()
