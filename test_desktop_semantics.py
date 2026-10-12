"""Discovery and action regressions using the real worker and a synthetic AT-SPI tree."""
import unittest
from unittest.mock import patch
from desktop_semantics import compact_score
try:
    from desktop_worker import Desktop
except ModuleNotFoundError as exc:
    raise unittest.SkipTest('Requires system Python GI') from exc


class Node:
    def __init__(self, name='', role='section', children=(), showing=True):
        self.name, self.role, self.parent = name, role, None
        self.children = list(children)
        self.states = ['sensitive'] + (['showing'] if showing else [])
        self.states += ['active'] if role == 'frame' else []
        self.actions = ['press'] if role in ('button', 'check box', 'page tab') else []
        self.interfaces = ['Component'] + (['Action'] if self.actions else [])
        self.pressed = 0
        for child in self.children:
            child.parent = self

    def get_name(self): return self.name
    def get_child_count(self): return len(self.children)
    def get_child_at_index(self, i): return self.children[i]
    def get_parent(self): return self.parent
    def get_action_iface(self): return self
    def do_action(self, i):
        self.pressed += 1
        if self.role == 'page tab': self.states.append('selected')
        return True


class TreeDesktop(Desktop):
    def describe(self, n):
        return {'name': n.name, 'role': n.role, 'states': list(n.states),
                'interfaces': n.interfaces, 'actions': n.actions}


class SemanticTests(unittest.TestCase):
    def start(self, children, **kwargs):
        win = Node('Browser', 'frame', children)
        app = Node('Firefox', 'application', [win])
        root = Node('Desktop', children=[app])
        p = patch('desktop_worker.Atspi.get_desktop', return_value=root)
        p.start(); self.addCleanup(p.stop)
        self.d = TreeDesktop()
        self.obs = self.d.observe(application='Firefox', **kwargs)
        return win

    def test_late_control_survives_400_wrappers_and_hidden_ancestors(self):
        target = Node('Save', 'button')
        wrapped = target
        for i in range(4): wrapped = Node('hidden'+str(i), children=[wrapped], showing=False)
        self.start([Node('wrapper') for _ in range(450)] + [wrapped], max_elements=1)
        self.assertEqual(self.obs['elements'][0]['name'], 'Save')
        self.assertTrue(self.obs['scan']['complete'])
        found = self.d.query(self.obs['snapshot_id'], {'name': 'Save', 'ancestor_name': 'hidden0'})
        self.assertEqual(len(found['elements']), 1)
        self.assertIn('hidden0', [e['name'] for e in self.d.nodes.values()])

    def test_pagination_keeps_snapshot_and_ancestor_projection_unchanged(self):
        self.start([Node('group', children=[Node(str(i), 'button') for i in range(8)])], max_elements=1)
        sid = self.obs['snapshot_id']
        a = self.d.query(sid, {'role': 'button'}, 3)
        b = self.d.query(sid, {'role': 'button'}, 3, a['next_offset'])
        self.assertEqual(self.d.snapshot['snapshot_id'], sid)
        self.assertFalse({x['id'] for x in a['elements']} & {x['id'] for x in b['elements']})
        self.assertNotEqual(self.d.nodes[a['elements'][0]['id']]['parent'], 'w1')

    def test_partial_scan_is_explicit_not_presentation_truncation(self):
        self.start([Node(str(i), 'button') for i in range(300)], max_elements=1, scan_limit=100)
        self.assertFalse(self.obs['scan']['complete'])
        self.assertIn('node_budget', self.obs['scan']['reasons'])
        self.assertTrue(self.obs['truncated'])

    def test_omitted_target_locator_executes_and_verifies_tab(self):
        tab = Node('Tab', 'page tab')
        self.start([Node('Attention', 'dialog'), tab], max_elements=1)
        self.assertNotIn('Tab', [e['name'] for e in self.obs['elements']])
        r = self.d.act(self.obs['snapshot_id'], [{'kind':'activate', 'locator':{'role':'page tab','name':'Tab'}}], wait_ms=0)
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['results'][0]['compact_hidden'])
        self.assertTrue(r['results'][0]['verified'])
        self.assertEqual(tab.pressed, 1)
        with self.assertRaisesRegex(ValueError, 'Stale snapshot'):
            self.d.act(self.obs['snapshot_id'], [{'kind':'activate','element':'e1'}])

    def test_ambiguity_and_context_revalidation_prevent_wrong_action(self):
        a, b = Node('Save','button'), Node('Save','button')
        region = Node('Document A','document web',[a])
        self.start([region, Node('Document B','document web',[b])])
        with self.assertRaisesRegex(ValueError, 'matched 2'):
            self.d.act(self.obs['snapshot_id'], [{'kind':'activate','locator':{'name':'Save'}}])
        region.name = 'Different document'
        with self.assertRaisesRegex(ValueError, 'context changed'):
            self.d.act(self.obs['snapshot_id'], [{'kind':'activate','locator':{'name':'Save','document':'Document A'}}])
        self.assertEqual(a.pressed + b.pressed, 0)

    def test_reparented_target_is_detected_even_when_its_attributes_match(self):
        button = Node('Save','button')
        first = Node('A',children=[button]); second = Node('B')
        self.start([first,second])
        button.parent = second
        with self.assertRaisesRegex(ValueError, 'ancestry changed'):
            self.d.act(self.obs['snapshot_id'], [{'kind':'activate','locator':{'name':'Save'}}])
        self.assertEqual(button.pressed,0)

    def test_unknown_postcondition_stops_batch_without_replaying(self):
        button = Node('Save','button')
        self.start([button])
        with patch('desktop_worker.time.monotonic', side_effect=range(100000)):
            r = self.d.act(self.obs['snapshot_id'], [{'kind':'activate','locator':{'name':'Save'},
                            'expect':{'states':{'expanded':True}}},
                            {'kind':'activate','locator':{'name':'Save'}}], wait_ms=0)
        self.assertFalse(r['ok'])
        self.assertEqual(button.pressed,1)
        self.assertTrue(r['results'][0]['executed'])
        self.assertFalse(r['results'][0]['verified'])

    def test_tiny_controls_remain_queryable_and_focused_controls_are_kept(self):
        e={'role':'button','name':'Prompt','bounds':[0,0,18,2],'states':['showing']}
        self.assertIsNone(compact_score(e))
        e['states'].append('focused')
        self.assertIsNotNone(compact_score(e))
