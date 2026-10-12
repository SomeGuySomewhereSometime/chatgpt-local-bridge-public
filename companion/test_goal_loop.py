import tempfile
import unittest
from coordinator import Coordinator

class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.c=Coordinator(self.tmp.name)
        self.base={'url':'https://chatgpt.com/c/loop-test','client':'client','tab':1}
        self.s={'document_id':'document-1234567890','state':'idle','user_count':2,'terminal':True,'marker':None}
        self.call('bind',project=self.tmp.name,task='')
        self.call('observe',state='idle')
    def tearDown(self):self.c.db.close();self.tmp.cleanup()
    def call(self,action,**p):return self.c.dispatch(dict(self.base,action=action,**p))
    def start(self,limit=3):return self.call('loop_start',goal='Test game rules. All tests pass.',limit=limit,snapshot=self.s)
    def delivered(self,r):return self.call('loop_result',id=r['send']['id'],state='observed_sent')
    def answer(self,r,decision='PENDING'):
        return dict(self.s,user_count=r['loop']['expected_users'],marker={'nonce':r['loop']['nonce'],'decision':decision})
    def test_bounded_loop_exact_count(self):
        r=self.start(3);self.assertEqual(r['loop']['used'],0);self.delivered(r)
        for used in range(1,4):
            r=self.call('loop_tick',snapshot=self.answer(r));self.assertEqual(r['loop']['used'],used);self.delivered(r)
        end=self.call('loop_tick',snapshot=self.answer(r))
        self.assertNotIn('send',end);self.assertEqual(end['loop']['state'],'limit_reached')
        self.assertEqual(len(self.call('status')['requests']),4)
    def test_done_and_needs_user(self):
        for decision,end in [('DONE','completed'),('NEEDS_USER','stopped')]:
            r=self.start();self.delivered(r)
            result=self.call('loop_tick',snapshot=self.answer(r,decision))
            self.assertEqual(result['loop']['state'],end);self.assertNotIn('send',result)
    def test_no_replay_after_process_restart(self):
        r=self.start();self.c.db.close();self.c=Coordinator(self.tmp.name)
        for _ in range(3):self.assertNotIn('send',self.call('loop_tick',snapshot=self.s))
        self.assertEqual(len(self.call('status')['requests']),1)
        with self.assertRaises(ValueError):self.call('claim',id=r['send']['id'])
    def test_missing_marker_old_nonce_human_message_reload_stop(self):
        for change in [{'marker':None},{'marker':{'nonce':'old','decision':'PENDING'}},{'user_count':44}]:
            r=self.start();self.delivered(r);s=self.answer(r);s.update(change)
            result=self.call('loop_tick',snapshot=s)
            self.assertEqual(result['loop']['state'],'stopped');self.assertNotIn('send',result)
    def test_blocking_states(self):
        for state in ['draft','approval','error','unknown','interrupted']:
            r=self.start();self.delivered(r)
            result=self.call('loop_tick',snapshot=dict(self.answer(r),state=state))
            self.assertEqual(result['loop']['state'],'stopped')
    def test_generation_only_waits(self):
        r=self.start();self.delivered(r)
        self.assertEqual(self.call('loop_tick',snapshot=dict(self.answer(r),state='generating'))['loop']['state'],'waiting')
    def test_expiry_duration(self):
        r=self.start();self.delivered(r)
        self.c.db.execute('UPDATE goal_loops SET last_seen=0');self.c.db.commit()
        self.assertEqual(self.call('status')['loop']['state'],'waiting')
        self.assertTrue(self.call('status')['loop']['observation_stale'])
        self.assertEqual(len(self.call('status')['requests']),1)
        self.call('loop_stop')
        r=self.start();self.delivered(r)
        self.c.db.execute('UPDATE goal_loops SET started=0');self.c.db.commit()
        self.assertEqual(self.call('status')['loop']['state'],'waiting')
        self.assertEqual(self.call('loop_tick',snapshot=dict(self.answer(r),state='generating'))['loop']['state'],'waiting')
    def test_stop_revokes_send_and_late_delivery_cannot_resume(self):
        r=self.start();self.call('loop_stop')
        self.assertFalse(self.call('loop_validate',id=r['send']['id'])['allowed'])
        self.assertEqual(self.delivered(r)['loop']['state'],'stopped')
    def test_uncertain_stops_manual_send_blocked(self):
        r=self.start()
        with self.assertRaises(ValueError):self.call('prepare',id='manual-request-1234',text='continue')
        result=self.call('loop_result',id=r['send']['id'],state='uncertain')
        self.assertEqual(result['loop']['state'],'stopped')
    def test_unbind_stops_and_preserves_receipt(self):
        r=self.start();self.call('unbind')
        self.assertEqual(self.c.db.execute('SELECT state FROM goal_loops').fetchone()[0],'stopped')
        self.assertEqual(self.c.db.execute('SELECT state FROM requests').fetchone()[0],'uncertain')
    def test_invalid_limit_goal_busy_no_dispatch(self):
        for extra in [{'limit':0},{'limit':11},{'limit':True},{'goal':''},{'snapshot':dict(self.s,state='draft')}]:
            p=dict(goal='goal',limit=3,snapshot=self.s);p.update(extra)
            with self.assertRaises(ValueError):self.call('loop_start',**p)
        self.assertEqual(len(self.call('status')['requests']),0)
    def test_atomic_tick_competition(self):
        import concurrent.futures
        r=self.start();self.delivered(r);s=self.answer(r)
        def tick():
            c=Coordinator(self.tmp.name)
            try:return c.dispatch(dict(self.base,action='loop_tick',snapshot=s))
            finally:c.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:tick(),range(2)))
        self.assertEqual(sum('send' in x for x in results),1)

    def test_throttled_observation_requires_fresh_matching_snapshot(self):
        r=self.start();self.delivered(r)
        self.c.db.execute('UPDATE goal_loops SET last_seen=0');self.c.db.commit()
        waiting=self.call('status')['loop']
        self.assertTrue(waiting['observation_stale'])
        for _ in range(3):
            self.assertNotIn('send',self.call('status'))
        result=self.call('loop_tick',snapshot=dict(self.answer(r),state='generating'))
        self.assertEqual(result['loop']['state'],'waiting')
        self.assertFalse(result['loop']['observation_stale'])
        result=self.call('loop_tick',snapshot=self.answer(r))
        self.assertIn('send',result)
        self.assertEqual(result['loop']['used'],1)

    def test_delayed_snapshot_cannot_bypass_reload_or_explicit_stop(self):
        for changed in [True,False]:
            r=self.start();self.delivered(r)
            self.c.db.execute('UPDATE goal_loops SET last_seen=0');self.c.db.commit()
            s=self.answer(r)
            if changed:s['document_id']='reloaded-document-12345'
            else:self.call('loop_stop')
            result=self.call('loop_tick',snapshot=s)
            self.assertNotIn('send',result)
            self.assertEqual(result['loop']['state'],'reconnect_required' if changed else 'stopped')
            self.call('loop_stop')

    def test_delayed_dispatch_permission_expires_without_replay(self):
        r=self.start()
        self.c.db.execute('UPDATE goal_loops SET last_seen=0');self.c.db.commit()
        self.assertFalse(self.call('loop_validate',id=r['send']['id'])['allowed'])
        self.assertEqual(self.delivered(r)['loop']['state'],'stopped')
        self.assertNotIn('send',self.call('loop_tick',snapshot=self.answer(r)))
        self.assertEqual(len(self.call('status')['requests']),1)
