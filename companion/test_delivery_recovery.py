import unittest,uuid
import test_work_context as h

class RecoveryTests(unittest.TestCase):
    setUp=h.WorkTests.setUp
    call=h.WorkTests.call
    start=h.WorkTests.start
    finish=h.WorkTests.finish
    checkpoint=h.WorkTests.checkpoint
    receipt=h.WorkTests.receipt

    def proof(self,r,**change):
        return {**self.snap,'url':self.base['url'],'user_count':r['loop']['expected_users'],
                'last_user_id':'message-new','last_user_text':r['send']['text'],'marker':{'nonce':r['loop']['nonce'],'decision':'DONE'},**change}

    def verify(self,r,**change):
        return self.call('verify_delivery',id=r['send']['id'],run_id=r['loop']['run_id'],snapshot=self.proof(r,**change))

    def uncertain(self,r,with_identity=False):
        self.call('loop_result',id=r['send']['id'],state='uncertain',observation={'document_id':r['loop']['document_id'],'user_count':r['loop']['expected_users'],'last_user_id':'message-new'} if with_identity else {})

    def test_legacy_exact_nonce_text_and_new_checkpoint_reconciles_completion(self):
        r=self.start();self.checkpoint(r);self.uncertain(r)
        before=len(self.call('status')['requests']);result=self.verify(r)
        self.assertTrue(result['verified']);self.assertEqual(result['loop']['state'],'completed')
        self.assertEqual(self.call('status')['requests'][0]['state'],'observed_sent')
        self.assertEqual(len(self.call('status')['requests']),before);self.assertNotIn('send',result)
        self.assertEqual(self.verify(r)['loop']['state'],'completed')
        self.assertTrue(any(e['kind']=='verify_delivery' for e in self.call('status')['events']))

    def test_final_review_completion_is_reconciled_without_another_review(self):
        r=self.start(final_review=True);self.checkpoint(r)
        review=self.finish(r);self.checkpoint(r,completed=['[BRIDGE_REVIEW:'+r['loop']['run_id']+'] Checked actual result and rechecked correction.'])
        self.uncertain(review,True);result=self.verify(review)
        self.assertEqual(result['loop']['state'],'completed')
        self.assertEqual(result['loop']['review_completed_revision'],2)
        self.assertEqual(result['loop']['used'],1);self.assertNotIn('send',result)

    def test_generating_draft_error_or_pending_never_resumes_automatic_messages(self):
        for state in ['generating','draft','approval','error','interrupted']:
            r=self.start();self.checkpoint(r);self.uncertain(r,True)
            result=self.verify(r,state=state,terminal=False)
            self.assertEqual(result['loop']['state'],'stopped');self.assertNotIn('send',result)
        r=self.start();self.checkpoint(r,next_step='Check real result');self.uncertain(r)
        self.assertEqual(self.verify(r)['loop']['state'],'stopped')

    def test_stop_halt_or_ambiguous_marker_never_recovers_continuation_permission(self):
        r=self.start();self.checkpoint(r);self.uncertain(r);self.c.db.execute("update goal_loops set reason='Stopped by user.'");self.c.db.commit()
        self.assertEqual(self.verify(r)['loop']['reason'],'Stopped by user.')
        r=self.start();self.checkpoint(r);self.uncertain(r)
        result=self.call('verify_delivery',id=r['send']['id'],run_id=r['loop']['run_id'],snapshot=self.proof(r),delivery_halted=True)
        self.assertEqual(result['loop']['state'],'stopped')
        r=self.start();self.checkpoint(r);self.uncertain(r)
        self.assertEqual(self.verify(r,marker={'nonce':'old','decision':'DONE'})['loop']['state'],'stopped')

    def test_prefix_wrong_text_id_count_or_url_never_confirms(self):
        for change in [{'last_user_text':'wrong'},{'last_user_text':''},{'last_user_text':None}, {'last_user_id':'wrong-id'}, {'last_user_id':''}, {'url':'https://chatgpt.com/c/other'}, {'user_count':2},{'state':'unknown'}]:
            r=self.start();self.uncertain(r,True)
            with self.assertRaises(ValueError):self.verify(r,**change)
            self.assertEqual(self.call('status')['requests'][0]['state'],'uncertain')

    def test_reloaded_page_uses_durable_candidate_identity(self):
        r=self.start();self.checkpoint(r);self.uncertain(r,True)
        self.assertEqual(self.verify(r,document_id='new-document-123456',user_count=1)['loop']['state'],'completed')

    def test_wrong_client_tab_run_or_receipt_is_rejected(self):
        r=self.start();self.uncertain(r)
        p={**self.base,'action':'verify_delivery','id':r['send']['id'],'run_id':r['loop']['run_id'],'snapshot':self.proof(r)}
        for change in [{'client':'foreign'},{'tab':10},{'run_id':'old'},{'id':'old'}]:
            with self.assertRaises(ValueError):self.c.dispatch({**p,**change})

    def test_newer_manual_attempt_prevents_using_a_stale_proof(self):
        r=self.start();self.uncertain(r)
        self.call('prepare',id=uuid.uuid4().hex,text='Later instruction')
        latest=self.call('status')['requests'][0]
        self.call('claim',id=latest['id'])
        with self.assertRaisesRegex(ValueError,'newer message'):self.verify(r)
        self.assertEqual(self.c.db.execute('SELECT state FROM requests WHERE id=?',(r['send']['id'],)).fetchone()[0],'uncertain')

    def test_active_delivery_cannot_be_reconciled(self):
        r=self.start()
        with self.assertRaisesRegex(ValueError,'Wait'):self.verify(r)
        self.assertEqual(self.call('status')['loop']['state'],'dispatching')

    def test_confirmed_message_does_not_override_unresolved_operations(self):
        r=self.start();self.checkpoint(r);self.receipt(r)
        with self.op.connection() as db:db.execute("update requests set state='unknown'")
        self.uncertain(r)
        self.assertEqual(self.verify(r)['loop']['state'],'stopped')
        self.assertEqual(self.call('status')['requests'][0]['state'],'observed_sent')

    def test_uncertain_chat_updates_are_confirmed_but_require_a_new_checkpoint(self):
        r=self.start();self.checkpoint(r)
        self.call('update_add',task_id=r['loop']['work_task_id'],id=uuid.uuid4().hex,text='Keep user change intact',mode='next_reply')
        next_reply=self.finish(r);self.uncertain(next_reply)
        result=self.verify(next_reply)
        self.assertEqual(self.call('status')['work']['updates'][0]['state'],'sent')
        self.assertEqual(result['loop']['state'],'stopped')
        self.checkpoint(r)
        self.assertEqual(self.verify(next_reply)['loop']['state'],'stopped') # Earlier reconciliation never silently re-enables a run.

if __name__=='__main__':unittest.main()
