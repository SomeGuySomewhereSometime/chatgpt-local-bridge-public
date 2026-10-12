"""Native continuity callbacks against temporary state; no editor/service actions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Gio,GLib,Gtk
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from continuity import ContinuityStore
from memory_store import MemoryStore
from interface.app import Application


def main():
    with tempfile.TemporaryDirectory(prefix='continuity-ui-') as tmp:
        root=Path(tmp)
        for name in ('project','private','control'):(root/name).mkdir()
        cfg=root/'control/config.json';memory=root/'private/memory/memory.sqlite3'
        cfg.write_text(json.dumps({'filesystem':{'denied_paths':[str(root/'private')],'read_only_paths':[str(root/'control')]},
            'memory':{'enabled':True,'path':str(memory)}}))
        os.environ.update(BRIDGE_WORKSPACE=tmp,BRIDGE_CONFIG=str(cfg),BRIDGE_MEMORY_DISABLED='0',BRIDGE_OPERATIONS_DIR=str(root/'private/operations'))
        store=ContinuityStore(MemoryStore(memory))
        task=store.begin(str(root/'project'),title='Teste de painel',goal='Retomar a validação',constraints=[],source='user',request_id='begin')
        store.checkpoint(str(root/'project'),task_id=task['task_id'],expected_revision=0,checkpoint={'completed':['Código implementado'],'unverified':['QA'],'next_step':'Executar QA'},state='paused',source='agent',request_id='checkpoint')
        app=Application();app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        phase=[0];success=[];deadline=time.monotonic()+60
        def tick():
            try:
                assert time.monotonic()<deadline,'UI callback deadline'
                w=app.get_active_window()
                if not w or w.busy or not w.snapshot_data:return True
                assert not w.feedback.has_css_class('feedback-error'),w.feedback.get_text()
                if phase[0]==0:
                    w.project.set_text(str(root/'project'))
                    w.navigation.select_row(w.navigation.get_row_at_index(6));w.load_continuity()
                elif phase[0]==1:
                    assert w.continuity_list.get_first_child() is not None
                    w.load_continuity(task['task_id'])
                elif phase[0]==2:
                    assert 'Executar QA' in w.continuity_details.get_text()
                    assert 'Por validar' in w.continuity_markdown
                    assert Gtk.FileDialog(title='Teste',initial_name='PROGRESSO.md') is not None
                    w.submit({'action':'continuity_export','path':str(root/'project/PROGRESSO.md'),
                              'content':w.continuity_markdown,'request_id':'export'})
                else:
                    assert 'Executar QA' in (root/'project/PROGRESSO.md').read_text()
                    print('PASS: native task list, checkpoint details and version-protected export; disposable data only')
                    success.append(True);app.quit();return False
                phase[0]+=1;return True
            except BaseException as e:
                print(type(e).__name__+': '+str(e),file=sys.stderr);app.quit();return False
        app.connect('activate',lambda *_:GLib.timeout_add(150,tick))
        app.run([])
        return 0 if success==[True] else 1


if __name__=='__main__':raise SystemExit(main())
