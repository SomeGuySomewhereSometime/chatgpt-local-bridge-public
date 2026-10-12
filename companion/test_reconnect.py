import json
import unittest
import test_goal_loop as helpers

class ReconnectTests(unittest.TestCase):
    setUp=helpers.LoopTests.setUp
    tearDown=helpers.LoopTests.tearDown
    call=helpers.LoopTests.call
    start=helpers.LoopTests.start
    delivered=helpers.LoopTests.delivered
    answer=helpers.LoopTests.answer
    def proof(self,r,**changes):
        return dict(self.answer(r),url=self.base['url'],document_id='reopened-document-12345',state='generating',last_user_id='message-verified',last_user_text=r['send']['text'],**changes)
    def reconnect(self,r,s=None,**changes):
        p={**self.base,'action':'reconnect','expected_tab':1,'expected_run_id':r['loop']['run_id'],'snapshot':s or self.proof(r),**changes}
        return self.c.dispatch(p)
    def test_reconnect_while_working_preserves_budget_nonce_and_receipt(self):
        r=self.start(2);self.delivered(r)
        self.c.db.execute('UPDATE goal_loops SET started=0');self.c.db.commit()
        result=self.reconnect(r,tab=2)
        self.assertNotIn('send',result);self.assertTrue(result['continuation_retained'])
        for key in ('run_id','nonce','used','max_steps','request_id','started'):
            self.assertEqual(result['loop'][key],self.call_with_tab('status',2)['loop'][key])
        self.assertEqual(result['loop']['used'],0);self.assertEqual(result['loop']['max_steps'],2)
        self.assertEqual(result['loop']['run_id'],r['loop']['run_id']);self.assertEqual(result['loop']['nonce'],r['loop']['nonce'])
        self.assertEqual(len(self.call_with_tab('status',2)['requests']),1)
        with self.assertRaises(ValueError):self.call('loop_validate',id=r['send']['id'])
        s=self.proof(r);s.update(state='completed',terminal=True)
        nxt=self.call_with_tab('loop_tick',2,snapshot=s)
        self.assertIn('send',nxt);self.assertEqual(nxt['loop']['used'],1)
        self.assertNotEqual(nxt['send']['id'],r['send']['id'])
        self.assertNotIn('send',self.call_with_tab('loop_tick',2,snapshot=s))
    def call_with_tab(self,action,tab,**p):return self.c.dispatch(dict(self.base,action=action,tab=tab,**p))
    def test_reload_pauses_until_explicit_verified_reconnect(self):
        r=self.start();self.delivered(r)
        s=self.proof(r)
        paused=self.call('loop_tick',snapshot=s)
        self.assertEqual(paused['loop']['state'],'reconnect_required')
        for _ in range(3):self.assertNotIn('send',self.call('loop_tick',snapshot=s))
        result=self.reconnect(r,s);self.assertTrue(result['continuation_retained'])
        self.assertEqual(result['loop']['state'],'waiting');self.assertNotIn('send',result)
    def test_stop_uncertain_or_halt_cannot_be_revived(self):
        for state in ['stop','uncertain','halt','dispatching']:
            r=self.start()
            if state!='dispatching':self.delivered(r)
            if state=='stop':self.call('loop_stop')
            if state=='uncertain':self.call('loop_result',id=r['send']['id'],state='uncertain') # preserve durable failed result below
            if state=='uncertain':
                self.c.db.execute("UPDATE goal_loops SET state='stopped',reason='Delivery uncertain' ");self.c.db.execute("UPDATE requests SET state='uncertain' WHERE id=?",(r['send']['id'],));self.c.db.commit()
            result=self.reconnect(r,delivery_halted=state=='halt')
            self.assertFalse(result['continuation_retained']);self.assertEqual(result['loop']['state'],'stopped');self.assertNotIn('send',result)
            self.assertNotIn('send',self.call('loop_tick',snapshot=self.proof(r)))
    def test_unverified_last_message_never_transfers_permission(self):
        for changed in [{'last_user_text':'Different human message'}, {'last_user_id':''},{'user_count':0},{'url':'https://chatgpt.com/c/other'},{'state':'unknown'}]:
            r=self.start();self.delivered(r);s=self.proof(r);s.update(changed);count=len(self.call('status')['requests'])
            if 'url' in changed or changed.get('state')=='unknown':
                with self.assertRaises(ValueError):self.reconnect(r,s,tab=2)
                self.assertEqual(self.call('status')['binding']['tab'],1)
            else:
                recovered=self.reconnect(r,s,tab=2)
                self.assertFalse(recovered['continuation_retained']);self.assertNotIn('send',recovered)
                self.assertEqual(recovered['loop']['state'],'stopped');self.assertEqual(recovered['binding']['tab'],2)
                self.reconnect(r,s,expected_tab=2,tab=1)
            self.assertEqual(len(self.call('status')['requests']),count)
            self.call('loop_stop')
    def test_foreign_client_stale_run_and_stale_binding_rejected(self):
        r=self.start();self.delivered(r)
        for change in [{'client':'other'},{'expected_tab':7},{'expected_run_id':'old'}]:
            with self.assertRaises(ValueError):self.reconnect(r,tab=2,**change)
        self.assertEqual(self.call('status')['binding']['tab'],1)
    def test_busy_page_error_draft_approval_do_not_enable_continuations(self):
        for state in ['draft','approval','error','interrupted']:
            r=self.start();self.delivered(r);s=self.proof(r);s['state']=state
            result=self.reconnect(r,s)
            self.assertFalse(result['continuation_retained']);self.assertEqual(result['loop']['state'],'stopped');self.assertNotIn('send',result)
    def test_cross_tab_read_only_inspection_cannot_send_or_mutate_binding(self):
        r=self.start();self.delivered(r)
        info=self.call_with_tab('connection_status',2)
        self.assertEqual(info['binding']['tab'],1);self.assertNotIn('send',info)
        with self.assertRaises(ValueError):self.call_with_tab('observe',2,state='generating')
        self.assertEqual(self.call('status')['binding']['tab'],1)
    def test_stop_diagnostics_do_not_label_successful_observation_rejected(self):
        r=self.start();self.delivered(r);self.call('loop_stop');self.call('observe',state='generating')
        export=self.call('diagnostics_export',run_id=r['loop']['run_id'])['diagnostic_export']
        observed=[e for e in export['events'] if e['action']=='observe']
        self.assertTrue(observed);self.assertEqual(observed[-1]['code'],'ok')
        self.assertNotIn(r['send']['text'],json.dumps(export))
    def test_prompt_distinguishes_stop_from_authority(self):
        r=self.start();self.assertIn('does not cancel the current response',r['send']['text'])
        self.assertIn('no wall-clock run limit',r['send']['text'])

import test_work_context as work_helpers
import uuid

class ReconnectWorkTests(unittest.TestCase):
    setUp=work_helpers.WorkTests.setUp
    call=work_helpers.WorkTests.call
    start=work_helpers.WorkTests.start
    checkpoint=work_helpers.WorkTests.checkpoint
    receipt=work_helpers.WorkTests.receipt
    def test_handoff_preserves_checkpoint_receipts_and_queued_updates(self):
        r=self.start();tid=r['loop']['work_task_id']
        self.call('loop_result',id=r['send']['id'],state='observed_sent')
        self.checkpoint(r,next_step='Finish browser checks')
        self.receipt(r)
        self.call('update_add',task_id=tid,id=uuid.uuid4().hex,text='Keep the current interface',mode='automatic')
        before=self.call('status')['work']
        result=self.c.dispatch({**self.base,'action':'reconnect','tab':8,'expected_tab':7,'expected_run_id':r['loop']['run_id'],
            'snapshot':{**self.snap,'url':self.base['url'],'state':'generating','document_id':'reopened-work-document','user_count':1,
                'last_user_id':'message-id','last_user_text':r['send']['text']}})
        self.assertTrue(result['continuation_retained']);self.assertNotIn('send',result)
        after=self.c.dispatch({**self.base,'action':'status','tab':8})['work']
        for key in ['task_id','context','receipts','updates','criteria']:
            self.assertEqual(after[key],before[key])

if __name__=='__main__':unittest.main()
