import tempfile
import unittest
from coordinator import Coordinator

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.c=Coordinator(self.root.name)
        self.addCleanup(self.c.db.close)
        self.p={'url':'https://chatgpt.com/c/workspace','client':'one','tab':7,'title':'Workspace'}
        self.c.dispatch({**self.p,'action':'bind','project':self.root.name})

    def test_client_scoped_list_and_disconnect(self):
        self.assertEqual(self.c.dispatch({'action':'list','client':'two'})['bindings'],[])
        self.assertEqual(len(self.c.dispatch({'action':'list','client':'one'})['bindings']),1)
        with self.assertRaisesRegex(ValueError,'another client'):
            self.c.dispatch({**self.p,'client':'two','action':'panel_disconnect'})
        self.assertEqual(len(self.c.dispatch({'action':'list','client':'one'})['bindings']),1)
        self.c.dispatch({**self.p,'action':'panel_disconnect'})
        self.assertEqual(self.c.dispatch({'action':'list','client':'one'})['bindings'],[])

    def test_transactional_activity_and_no_poll_noise(self):
        self.c.dispatch({**self.p,'action':'observe','state':'idle'})
        self.c.dispatch({**self.p,'action':'observe','state':'idle'})
        info=self.c.dispatch({**self.p,'action':'status'})
        self.assertEqual([e['kind'] for e in info['events']],['observe','bind'])
        self.c.dispatch({**self.p,'action':'prepare','id':'request-workspace-123456','text':'Test message'})
        self.c.dispatch({**self.p,'action':'claim','id':'request-workspace-123456'})
        self.c.dispatch({**self.p,'action':'result','id':'request-workspace-123456','state':'observed_sent'})
        info=self.c.dispatch({**self.p,'action':'status'})
        self.assertEqual(info['events'][0]['kind'],'result')
        self.assertEqual(info['requests'][0]['state'],'observed_sent')
        before=len(info['events'])
        with self.assertRaises(ValueError):
            self.c.dispatch({**self.p,'action':'claim','id':'request-workspace-123456'})
        self.assertEqual(len(self.c.dispatch({**self.p,'action':'status'})['events']),before)
        self.assertNotIn('Test message',str(info['events']))

if __name__=='__main__':unittest.main()
