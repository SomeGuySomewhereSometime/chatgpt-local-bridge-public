import tempfile
import unittest
from coordinator import Coordinator, conversation

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.c=Coordinator(self.tmp.name)
        self.p=dict(url='https://chatgpt.com/c/test-123',client='client',tab=7)
        self.call('bind',project=self.tmp.name,task='task-1')
    def tearDown(self):self.c.db.close();self.tmp.cleanup()
    def call(self,action,**kw):return self.c.dispatch(dict(self.p,action=action,**kw))
    def prepare(self):return self.call('prepare',id='request-1234567890',text='Continua')
    def test_routes(self):
        self.assertEqual(conversation('https://chatgpt.com/g/project/c/abc'),'abc')
        for url in ['https://evil.com/c/abc','https://chatgpt.com/','https://chatgpt.com.evil/c/abc']:
            with self.assertRaises(ValueError):conversation(url)
    def test_replay_after_restart(self):
        self.call('observe',state='completed');self.prepare();self.call('claim',id='request-1234567890')
        self.c.db.close();self.c=Coordinator(self.tmp.name)
        with self.assertRaises(ValueError):self.call('claim',id='request-1234567890')
    def test_draft_generation_unknown_blocked(self):
        self.prepare()
        for state in ['draft','generating','unknown','approval','error']:
            self.call('observe',state=state)
            with self.assertRaises(ValueError):self.call('claim',id='request-1234567890')
    def test_wrong_tab(self):
        with self.assertRaises(ValueError):self.c.dispatch(dict(self.p,action='observe',tab=8,state='completed'))
    def test_stale(self):
        self.call('observe',state='completed');self.prepare()
        self.c.db.execute('UPDATE bindings SET seen=0');self.c.db.commit()
        with self.assertRaises(ValueError):self.call('claim',id='request-1234567890')
    def test_send_receipt(self):
        self.call('observe',state='completed');self.prepare();self.call('claim',id='request-1234567890')
        self.assertEqual(self.call('result',id='request-1234567890',state='observed_sent')['state'],'observed_sent')
        with self.assertRaises(ValueError):self.call('claim',id='request-1234567890')
    def test_id_collision(self):
        self.prepare()
        with self.assertRaises(ValueError):self.call('prepare',id='request-1234567890',text='Different')
    def test_cancel_unbind(self):
        self.prepare();self.call('unbind')
        self.assertEqual(self.c.dispatch({'action':'list'})['requests'][0]['state'],'cancelled')

if __name__=='__main__':unittest.main()

class OptionalTaskTests(Tests):
    def test_optional_identity_survives_rename_restart(self):
        a=self.call('bind',project=self.tmp.name,task='',title='Sueca')
        self.assertTrue(a['task_id'].startswith('local-'))
        self.c.db.close();self.c=Coordinator(self.tmp.name)
        b=self.call('bind',project=self.tmp.name,task='',title='Renamed conversation')
        self.assertEqual(a['task_id'],b['task_id'])
        status=self.call('status')['binding']
        self.assertEqual(status['task'],'');self.assertEqual(status['title'],'Renamed conversation')
    def test_rebind_cancels_old_preview(self):
        self.prepare();self.call('bind',project=self.tmp.name,task='different reference')
        self.assertEqual(self.call('status')['requests'][0]['state'],'cancelled')
    def test_status_wrong_tab_blocked(self):
        with self.assertRaises(ValueError):self.c.dispatch(dict(self.p,action='status',tab=8))
    def test_migrates_old_database_without_losing_records(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db=sqlite3.connect(directory+'/state.sqlite3')
            db.execute('CREATE TABLE bindings(conversation TEXT PRIMARY KEY,project TEXT NOT NULL,task TEXT NOT NULL,client TEXT NOT NULL,tab INTEGER NOT NULL,state TEXT NOT NULL,seen REAL NOT NULL)')
            db.execute('INSERT INTO bindings VALUES(?,?,?,?,?,?,?)',('old','/tmp','legacy-task','client',7,'idle',0))
            db.commit();db.close()
            migrated=Coordinator(directory)
            row=migrated.dispatch({'action':'list'})['bindings'][0]
            self.assertEqual(row['task'],'legacy-task');self.assertTrue(row['task_id'].startswith('local-'))
            migrated.db.close()
