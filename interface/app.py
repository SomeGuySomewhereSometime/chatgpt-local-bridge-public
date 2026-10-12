#!/usr/bin/python3
"""Native GTK4 control panel for the NOBUBBLEWARP bridge."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if not (ROOT / '.venv/bin/python').exists():
    ROOT = Path('/home/bridge-user/chatgpt-local-bridge-NOBUBBLEWARP')

STATE_NAMES = {'active': 'Ativo', 'inactive': 'Parado', 'failed': 'Falhou', 'unknown': 'Não verificado',
               'activating': 'A iniciar', 'deactivating': 'A parar', 'completed': 'Concluída',
               'accepted': 'Aceite', 'running': 'Em curso', 'resolved': 'Verificada', 'cancelled': 'Cancelado', 'timed_out': 'Tempo esgotado'}
CSS = '''
window { background: #171c24; color: #e9edf3; }
.sidebar { background: #11161e; border-right: 1px solid #29323f; }
.nav { padding: 10px 12px; border-radius: 9px; }
.nav:selected { background: #243a42; color: #69e0d0; }
.page { padding: 28px; }
.card { background: #202731; border: 1px solid #323c49; border-radius: 14px; padding: 20px; }
.section-row { padding: 14px 0; border-bottom: 1px solid #323c49; }
.title { font-size: 27px; font-weight: 750; }
.hero { font-size: 31px; font-weight: 750; }
.section-title { font-size: 17px; font-weight: 700; }
.muted { color: #9daabc; }
.value { font-size: 21px; font-weight: 700; }
.pill { background: #183f40; color: #76e3d0; border-radius: 20px; padding: 5px 10px; font-size: 11px; font-weight: 750; }
.warning { background: #443923; color: #f4d085; border-radius: 10px; padding: 14px; }
.feedback { background: #223a3d; color: #c4eee7; border-radius: 10px; padding: 12px; }
.feedback-error { background: #462932; color: #ffcad5; border-radius: 10px; padding: 12px; }
.success { color: #76e3d0; }
.caution { color: #f4d085; }
.small { font-size: 12px; }
button.suggested-action { background: #277f77; color: white; }
'''


def label(text='', style='', wrap=False):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap, selectable=False)
    for name in style.split():
        widget.add_css_class(name)
    return widget


def box(vertical=True, spacing=12, style=''):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                     spacing=spacing)
    for name in style.split():
        widget.add_css_class(name)
    return widget


def clear(widget):
    child = widget.get_first_child()
    while child:
        following = child.get_next_sibling()
        widget.remove(child)
        child = following


class BridgeWindow(Adw.ApplicationWindow):
    def __init__(self, application, demo=False):
        super().__init__(application=application, title='Bridge Control · NOBUBBLEWARP')
        self.set_default_size(1120, 820)
        self.set_size_request(860, 650)
        self.demo = demo
        self.busy = False
        self.destroyed = False
        self.owner = 'interface:' + uuid.uuid4().hex
        self.token = ''
        self.reserved_project = ''
        self.lease_expiry = 0
        self.buttons = []
        self.snapshot_data = None
        self.continuity_markdown = ''
        self.continuity_task_id = ''
        self.continuity_project = ''
        self.connect('close-request', self.closed)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title='Bridge Control', subtitle='NOBUBBLEWARP')
        header.set_title_widget(title)
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        refresh = self.button('Atualizar', self.refresh, icon='view-refresh-symbolic')
        header.pack_end(refresh)
        toolbar.add_top_bar(header)
        shell = box(False, 0)
        toolbar.set_content(shell)
        sidebar = box(style='sidebar', spacing=18)
        sidebar.set_size_request(205, -1)
        sidebar.set_margin_top(24); sidebar.set_margin_bottom(20)
        brand = box(spacing=8)
        brand.set_margin_start(20); brand.set_margin_end(20)
        brand.append(Gtk.Image.new_from_file(str(HERE / 'bridge-control.svg')))
        brand.get_first_child().set_pixel_size(42)
        brand.append(label('O teu PC. O teu fluxo.', 'section-title', wrap=True))
        brand.append(label('Controlo local da bridge', 'muted small'))
        sidebar.append(brand)
        self.navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.navigation.add_css_class('navigation-sidebar')
        sidebar.append(self.navigation)
        spacer = box(); spacer.set_vexpand(True); sidebar.append(spacer)
        note = box(spacing=7); note.set_margin_start(20); note.set_margin_end(20)
        note.append(label('NOBUBBLEWARP', 'pill'))
        note.append(label('Execução direta no host\nEstado e dados próprios', 'muted small', wrap=True))
        sidebar.append(note)
        shell.append(sidebar)
        content = box(spacing=0); content.set_hexpand(True)
        self.feedback = label('', 'feedback', wrap=True)
        self.feedback.set_margin_start(28); self.feedback.set_margin_end(28)
        self.feedback.set_margin_top(16)
        self.feedback.set_visible(False)
        content.append(self.feedback)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True); content.append(self.stack)
        shell.append(content)
        self.pages = {}
        for key, name, icon in [('overview', 'Visão geral', 'view-grid-symbolic'),
                                ('integrations', 'Integrações', 'network-wired-symbolic'),
                                ('projects', 'Projetos', 'folder-symbolic'),
                                ('operations', 'Operações', 'view-list-symbolic'),
                                ('reservations', 'Reservas', 'changes-prevent-symbolic'),
                                ('connection', 'Ligação e ajuda', 'help-browser-symbolic'),
                                ('continuity', 'Continuidade', 'document-save-symbolic'),
                                ('companion', 'ChatGPT in Chrome', 'web-browser-symbolic')]:
            row = Gtk.ListBoxRow(); row.add_css_class('nav'); row.page_key = key
            body = box(False, 10); body.append(Gtk.Image.new_from_icon_name(icon)); body.append(label(name))
            row.set_child(body); self.navigation.append(row)
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            page = box(spacing=22, style='page'); scroll.set_child(page)
            self.pages[key] = page; self.stack.add_named(scroll, key)
        self.navigation.connect('row-selected', lambda _, row: self.stack.set_visible_child_name(row.page_key) if row else None)
        self.build_overview(); self.build_integrations(); self.build_projects()
        self.build_operations(); self.build_reservations(); self.build_connection(); self.build_continuity(); self.build_companion()
        self.navigation.select_row(self.navigation.get_row_at_index(0))
        self.set_content(toolbar)
        GLib.idle_add(self.refresh)
        self.timer = GLib.timeout_add_seconds(25, self.auto_refresh)

    def closed(self, *_):
        self.destroyed = True
        if self.timer:
            GLib.source_remove(self.timer); self.timer = 0
        # Reservations expire naturally; never dispatch a hidden mutation on close.
        return False

    def button(self, text, action, suggested=False, icon=None):
        widget = Gtk.Button(label=text)
        if icon:
            content = box(False, 7); content.append(Gtk.Image.new_from_icon_name(icon)); content.append(label(text))
            widget.set_child(content)
        if suggested:
            widget.add_css_class('suggested-action')
        widget.connect('clicked', lambda *_: action())
        self.buttons.append(widget)
        return widget

    def heading(self, page, title, subtitle):
        page.append(label(title, 'title'))
        page.append(label(subtitle, 'muted', wrap=True))

    def card(self, page, title=None):
        card = box(style='card')
        if title:
            card.append(label(title, 'section-title'))
        page.append(card)
        return card

    def build_overview(self):
        page = self.pages['overview']
        page.append(label('O teu PC, ligado ao chat.', 'hero'))
        page.append(label('Acompanha a bridge e prepara as ferramentas quando precisares.', 'muted', wrap=True))
        stats = box(False, 14)
        self.values = {}
        for key, title in [('connection', 'Ligação ao ChatGPT'), ('server', 'Servidor local'), ('active', 'Operações ativas')]:
            tile = box(style='card'); tile.set_hexpand(True)
            tile.append(label(title, 'muted small'))
            value = label('A verificar…', 'value', wrap=True); tile.append(value)
            self.values[key] = value; stats.append(tile)
        page.append(stats)
        card = self.card(page, 'Bridge NOBUBBLEWARP')
        card.append(label('Os comandos usam as permissões da tua sessão no PC, com acesso aos sockets e ferramentas do host.',
                          'muted', wrap=True))
        self.service_text = label('A ler estado do serviço…', wrap=True); card.append(self.service_text)
        actions = box(False, 8)
        self.start_button = self.button('Iniciar ligação', lambda: self.submit({'action': 'service', 'command': 'start'}), True)
        self.stop_button = self.button('Parar', lambda: self.submit({'action': 'service', 'command': 'stop'}))
        self.restart_button = self.button('Reiniciar', lambda: self.submit({'action': 'service', 'command': 'restart'}))
        self.diagnose_button = self.button('Testar servidor local', lambda: self.submit({'action': 'diagnose'}))
        for button in (self.start_button, self.stop_button, self.restart_button, self.diagnose_button):
            actions.append(button)
        card.append(actions)
        self.local_test_text = label('O teste local confirma o catálogo MCP. A ligação hospedada é verificada separadamente.', 'muted small', wrap=True)
        card.append(self.local_test_text)
        card = self.card(page, 'Novas capacidades')
        for title, description in [('Ambientes por projeto', 'Caches persistentes e ambiente Python opcional.'),
                                   ('Alterações com recibos', 'Versões de ficheiros e consulta após perda de resposta.'),
                                   ('Coordenação entre chats', 'Reservas temporárias para projetos, desktop e editores.')]:
            card.append(label(title, 'section-title')); card.append(label(description, 'muted', wrap=True))

    def build_integrations(self):
        page = self.pages['integrations']
        self.heading(page, 'Integrações', 'Serviços partilhados com a bridge original. Preparar inicia apenas serviços parados; não abre aplicações.')
        self.integration_labels = {}
        card = self.card(page)
        for key, name in [('browser', 'Browser'), ('unity', 'Unity'), ('blender', 'Blender'), ('godot', 'Godot'), ('libresprite', 'LibreSprite')]:
            row = box(False, 12, 'section-row')
            text = box(spacing=5); text.set_hexpand(True)
            text.append(label(name, 'section-title'))
            state = label('A verificar serviço…', 'muted small', wrap=True); text.append(state)
            self.integration_labels[key] = state; row.append(text)
            row.append(self.button('Verificar', lambda k=key: self.submit({'action': 'probe', 'component': k})))
            row.append(self.button('Preparar', lambda k=key: self.submit({'action': 'prepare', 'component': k})))
            card.append(row)
        page.append(label('Um serviço ativo não comprova que o editor ou a extensão do browser estão ligados. Usa Verificar e confirma a utilização pelo respetivo plugin.', 'muted small', wrap=True))

    def build_projects(self):
        page = self.pages['projects']
        self.heading(page, 'Projetos', 'Prepara caches e ferramentas persistentes para a pasta em que estás a trabalhar.')
        card = self.card(page, 'Pasta do projeto')
        row = box(False, 8)
        self.project = Gtk.Entry(text=str(Path.home()), hexpand=True)
        self.project.set_placeholder_text('Escolhe a raiz do projeto')
        row.append(self.project); row.append(self.button('Escolher…', self.choose_project)); card.append(row)
        actions = box(False, 8)
        actions.append(self.button('Ver ambiente', lambda: self.project_action('status')))
        actions.append(self.button('Preparar caches', lambda: self.project_action('prepare'), True))
        actions.append(self.button('Criar ambiente Python', lambda: self.project_action('prepare', python=True)))
        card.append(actions)
        self.project_details = label('Nenhum ambiente selecionado. As dependências do projeto não são instaladas automaticamente.', 'muted', wrap=True)
        self.project_details.set_selectable(True); card.append(self.project_details)
        reservation = self.card(page, 'Reservar este projeto')
        reservation.append(label('Evita alterações concorrentes por outros chats desta bridge durante 5 minutos. As reservas são cooperativas e precisam de renovação.', 'muted', wrap=True))
        actions = box(False, 8)
        actions.append(self.button('Reservar 5 minutos', lambda: self.lease_action('acquire')))
        actions.append(self.button('Renovar', lambda: self.lease_action('renew')))
        actions.append(self.button('Libertar', lambda: self.lease_action('release')))
        reservation.append(actions)
        self.lease_text = label('Esta interface ainda não reservou um projeto.', 'muted small', wrap=True)
        reservation.append(self.lease_text)

    def build_operations(self):
        page = self.pages['operations']
        self.heading(page, 'Operações', 'Últimos 40 recibos desta variante. Os conteúdos de ficheiros, imagens e comandos não são guardados aqui.')
        self.operation_list = self.card(page)
        page.append(label('Resultado desconhecido significa que pode ter havido um efeito. Verifica o estado antes de repetir a ação. Concluída indica término da operação, não validação do objetivo.', 'muted small', wrap=True))

    def build_reservations(self):
        page = self.pages['reservations']
        self.heading(page, 'Reservas', 'Projetos, desktop e editores reservados por clientes desta bridge. Os tokens ficam ocultos.')
        self.reservation_list = self.card(page)
        page.append(label('As reservas expiram automaticamente. Não bloqueiam editores externos, outros MCPs ou acesso livre pelo shell. A interface não força a libertação de reservas de outros chats.', 'muted small', wrap=True))

    def build_connection(self):
        page = self.pages['connection']
        self.heading(page, 'Ligação e ajuda', 'A interface é local. A ligação ao ChatGPT usa um túnel próprio desta variante.')
        card = self.card(page, 'Configuração da NOBUBBLEWARP')
        self.profile_text = label('A verificar perfil…', wrap=True); card.append(self.profile_text)
        card.append(label('1. Configurar um túnel separado para esta variante.\n2. Usar start.sh desta pasta como servidor MCP stdio.\n3. Associar o túnel à ligação NOBUBBLEWARP no ChatGPT.\n4. Atualizar o catálogo de ferramentas da ligação quando mudar.', 'muted', wrap=True))
        card.append(label('A interface não instala nem substitui o túnel original. O serviço só pode ser controlado quando já estiver instalado.', 'muted small', wrap=True))
        card = self.card(page, 'Pasta e documentação')
        location = label(str(ROOT), 'muted', wrap=True); location.set_selectable(True); card.append(location)
        card.append(self.button('Abrir pasta da variante', lambda: Gio.AppInfo.launch_default_for_uri(ROOT.as_uri(), None)))
        card.append(label('README.md explica o arranque, o túnel separado e as limitações.\nVERIFICATION.md contém as verificações efetuadas.', 'muted', wrap=True))

    def choose_project(self):
        chooser = Gtk.FileChooserNative(title='Escolher projeto', transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER, accept_label='Escolher', cancel_label='Cancelar')
        def selected(dialog, response):
            if response == Gtk.ResponseType.ACCEPT and dialog.get_file():
                path = dialog.get_file().get_path()
                if path:
                    self.project.set_text(path)
            dialog.destroy()
        chooser.connect('response', selected); chooser.show()

    def project_action(self, command, python=False):
        self.submit({'action': 'project', 'command': command, 'cwd': self.project.get_text(), 'python': python})

    def lease_action(self, command):
        if self.reserved_project and time.time() >= self.lease_expiry:
            self.reserved_project = ''; self.token = ''
            self.lease_text.set_text('A reserva desta interface expirou. Podes reservar novamente.')
        resource = 'path:' + str(Path(self.project.get_text()).expanduser().resolve())
        if command == 'acquire' and self.reserved_project and resource != self.reserved_project:
            self.message('Liberta primeiro o projeto que esta interface já reservou.', True)
            return
        if command in {'renew', 'release'} and resource != self.reserved_project:
            self.message('Seleciona o projeto que esta interface reservou.', True)
            return
        self.submit({'action': 'lease', 'command': command, 'resource': resource})

    def build_companion(self):
        page = self.pages['companion']
        self.heading(page, 'ChatGPT in Chrome', 'Connect a conversation, review messages and explicitly start bounded goal loops.')
        page.append(label('Keep working in ChatGPT. The extension observes connected conversations; this page shows connections, loop progress and delivery receipts. Open Bridge Workspace to select conversations, review goals and start loops.', 'muted', wrap=True))
        page.append(self.button('Open Bridge Workspace', lambda: self.submit({'action':'companion_open'}), suggested=True))
        page.append(self.button('Refresh conversations', lambda: self.submit({'action':'companion'}), suggested=True))
        self.companion_list = box(spacing=12, style='card'); page.append(self.companion_list)
        self.companion_list.append(label('Open the extension in your ChatGPT conversation and choose Connect this conversation.', wrap=True))
        page.append(label('Task names and references are optional. A stable internal ID is kept automatically. To use an existing checkpoint, copy it from Continuity and review it in the extension before sending.', 'muted', wrap=True))
        path = label(str(ROOT / 'companion/extension'), 'muted', wrap=True)
        path.set_selectable(True); page.append(path)

    def build_continuity(self):
        page = self.pages['continuity']
        page.append(label('Continuidade', 'title'))
        page.append(label('Pontos de situação por tarefa. São contexto histórico, não o estado vivo do ChatGPT.', 'muted', wrap=True))
        self.continuity_root = label('Usa a pasta selecionada em Projetos.', 'muted small', wrap=True)
        page.append(self.continuity_root)
        actions = box(False, 10)
        actions.append(self.button('Consultar tarefas', self.load_continuity, suggested=True))
        actions.append(self.button('Copiar para continuar', self.copy_continuation))
        actions.append(self.button('Exportar Markdown…', self.export_continuity))
        page.append(actions)
        self.continuity_list = box(spacing=10, style='card'); page.append(self.continuity_list)
        self.continuity_list.append(label('Ainda não consultaste as tarefas deste projeto.', 'muted', wrap=True))
        self.continuity_details = label('', 'muted', wrap=True)
        self.continuity_details.set_selectable(True); page.append(self.continuity_details)
        page.append(label('Sem atividade recente não significa que o agente esteja bloqueado. Retomar exige verificar ficheiros e recibos; o painel nunca repete o próximo passo.', 'muted small', wrap=True))

    def load_continuity(self, task_id=''):
        project = self.project.get_text().strip()
        if project != self.continuity_project:
            self.continuity_markdown = ''; self.continuity_task_id = ''
            self.continuity_details.set_text('')
        self.continuity_project = project
        self.continuity_root.set_text('Projeto: ' + project)
        self.submit({'action': 'continuity', 'cwd': project, 'task_id': task_id})

    def copy_continuation(self):
        if not self.continuity_markdown:
            self.message('Seleciona primeiro uma tarefa.'); return
        prompt = ('Retoma apenas o âmbito já autorizado desta tarefa. Lê este registo histórico, verifica os ficheiros e o estado das operações antes de agir. Não repitas ações com resultado desconhecido.\n\n' + self.continuity_markdown)
        self.get_display().get_clipboard().set(prompt)
        self.message('Ponto de situação copiado. Cola-o no chat onde queres continuar.')

    def export_continuity(self):
        if not self.continuity_markdown:
            self.message('Seleciona primeiro uma tarefa.'); return
        dialog = Gtk.FileDialog(title='Exportar ponto de situação', initial_name='PROGRESSO.md')
        content = self.continuity_markdown
        def saved(_, result):
            try:
                file = dialog.save_finish(result)
                path = file.get_path()
                if path:
                    self.submit({'action':'continuity_export','path':path,'content':content,
                                 'request_id':'interface-export-' + uuid.uuid4().hex})
            except GLib.Error:
                pass
        dialog.save(self, None, saved)

    def message(self, text, error=False):
        self.feedback.set_text(text)
        self.feedback.remove_css_class('feedback-error')
        if error:
            self.feedback.add_css_class('feedback-error')
        self.feedback.set_visible(True)

    def auto_refresh(self):
        if not self.busy and not self.destroyed:
            self.refresh(quiet=True)
        return not self.destroyed

    def refresh(self, quiet=False):
        self.submit({'action': 'snapshot'}, quiet=quiet)
        return False

    def submit(self, payload, quiet=False):
        if self.busy or self.destroyed:
            return
        if self.demo:
            if payload['action'] == 'snapshot':
                self.render(demo_snapshot())
            else:
                self.message('Modo de pré-visualização: nenhuma ação foi executada.')
            return
        payload = dict(payload, owner_id=self.owner, lease_token=self.token)
        if payload['action'] == 'prepare' or (payload['action'] == 'project' and payload['command'] == 'prepare'):
            payload['request_id'] = 'interface-' + uuid.uuid4().hex
        self.busy = True; self.spinner.start()
        for button in self.buttons:
            button.set_sensitive(False)
        def worker():
            try:
                process = subprocess.run([str(ROOT / '.venv/bin/python'), '-B', str(HERE / 'backend.py')],
                    input=json.dumps(payload), capture_output=True, text=True, timeout=130,
                    env=panel_environment())
                if process.returncode:
                    result = {'ok': False, 'error': 'O controlador terminou sem resposta. Verifica o estado antes de repetir.'}
                else:
                    result = json.loads(process.stdout)
            except Exception:
                result = {'ok': False, 'error': 'Não foi possível confirmar a resposta. Consulta os recibos antes de repetir.'}
            GLib.idle_add(self.finished, payload, result, quiet)
        threading.Thread(target=worker, daemon=True).start()

    def finished(self, payload, result, quiet):
        if self.destroyed:
            return False
        self.busy = False; self.spinner.stop()
        for button in self.buttons:
            button.set_sensitive(True)
        self.apply_service_buttons()
        if not result.get('ok', False):
            text = result.get('error', 'A operação não ficou confirmada. Verifica o estado antes de repetir.')
            if payload.get('request_id'):
                text += '\nPedido: ' + payload['request_id']
            self.message(text, True)
            return False
        action = payload['action']
        if action == 'snapshot':
            self.render(result)
        elif action == 'companion':
            clear(self.companion_list)
            states = {'idle':'Ready to continue', 'completed':'Response finished', 'generating':'Responding',
                      'interrupted':'Response stopped', 'draft':'Draft open', 'approval':'Waiting for approval',
                      'unknown':'State not confirmed', 'disconnected':'Connection lost', 'error':'Page error',
                      'pending':'Preview only — not sent', 'observed_sent':'Message sent',
                      'uncertain':'Delivery uncertain — check conversation', 'cancelled':'Cancelled'}
            for item in result.get('bindings', []):
                row = box(spacing=6)
                row.append(label((item.get('title') or item['task'] or 'ChatGPT conversation') + ' · ' + states.get(item['state'], item['state']), 'section-title', wrap=True))
                row.append(label(item['project'] + '\nConversation: ' + item['conversation'], 'muted', wrap=True))
                loop = item.get('loop')
                if loop:
                    row.append(label('Goal: ' + loop['goal'], wrap=True))
                    row.append(label(f"Loop: {loop['state']} · {loop['used']}/{loop['max_steps']} automatic continuations\n{loop['reason']}", 'muted', wrap=True))
                    if loop['state'] in {'waiting', 'dispatching'}:
                        row.append(self.button('Stop loop', lambda cid=item['conversation']: self.submit({'action':'companion_stop','url':'https://chatgpt.com/c/'+cid})))
                row.append(self.button('Disconnect', lambda cid=item['conversation']: self.submit({'action':'companion_unbind','url':'https://chatgpt.com/c/'+cid})))
                self.companion_list.append(row)
            for receipt in result.get('requests', []):
                self.companion_list.append(label(receipt['id'][:12] + ' · ' + states.get(receipt['state'], receipt['state']), 'muted', wrap=True))
            if not result.get('bindings'):
                self.companion_list.append(label('No conversations connected yet.'))
        elif action == 'companion_open':
            self.message('Bridge Workspace opened in Chrome. After an extension update, reload its card and safely reload the ChatGPT tab.')
        elif action == 'companion_stop':
            self.message('Loop stopped. In-flight messages and already running tools may finish. Refresh to see its state.')
        elif action == 'companion_unbind':
            self.message('Conversation disconnected. Unsent previews cancelled; previous delivery receipts kept.')
        elif action == 'diagnose':
            self.local_test_text.set_text(f"Servidor MCP local verificado: {result['tools']} ferramentas. Não comprova a ligação hospedada.")
            self.message('O servidor local respondeu e apresentou as ferramentas da NOBUBBLEWARP.')
        elif action == 'probe':
            state = 'Pronta localmente' if result['integration_ready'] else 'Aplicação/MCP ainda não prontos'
            self.integration_labels[result['component']].set_text(state + ' · ligação ao ChatGPT por verificar')
            self.message('Verificação da integração concluída.')
        elif action == 'continuity':
            if payload.get('task_id'):
                self.continuity_task_id = payload['task_id']
                self.continuity_markdown = result['markdown']
                self.continuity_details.set_text(self.continuity_markdown)
                self.message('Registo histórico carregado; confirma o estado real antes de agir.')
            else:
                clear(self.continuity_list)
                states = {'in_progress':'Em curso (declarado)', 'paused':'Pausada', 'blocked':'Bloqueada', 'completed':'Concluída (declarado)'}
                for task in result.get('tasks', []):
                    row = box(spacing=5)
                    updated = time.strftime('%Y-%m-%d %H:%M', time.localtime(task['updated_at']))
                    row.append(label(task['title'] + ' · ' + states.get(task['state'], task['state']), 'section-title', wrap=True))
                    row.append(label('Revisão ' + str(task['revision']) + ' · último registo ' + updated, 'muted small'))
                    button = Gtk.Button(label='Ver ponto de situação')
                    button.connect('clicked', lambda _, key=task['id']: self.load_continuity(key))
                    row.append(button); self.continuity_list.append(row)
                if not result.get('tasks'):
                    self.continuity_list.append(label('Memória desativada.' if result.get('status') == 'disabled' else 'Não há tarefas registadas neste projeto.', 'muted', wrap=True))
                if result.get('has_more'):
                    self.continuity_list.append(label('Há mais tarefas. Usa task_context para consultar a lista com um limite maior.', 'muted small', wrap=True))
        elif action == 'continuity_export':
            self.message('Ponto de situação exportado: ' + result.get('path', ''))
        elif action == 'project':
            self.project_details.set_text('Projeto: ' + result['project_root'] + '\nCache: ' + result['cache_directory'] +
                '\nPython: ' + (result.get('python_environment') or 'Ambiente Python do host'))
            self.message('Ambiente do projeto consultado.' if payload['command'] == 'status' else 'Ambiente persistente preparado.')
        elif action == 'lease':
            if payload['command'] == 'release':
                self.reserved_project = ''; self.token = ''
                self.lease_expiry = 0
                self.lease_text.set_text('Reserva libertada.')
            else:
                self.token = result['lease_token']; self.reserved_project = result['resource']
                self.lease_expiry = result['expires_at']
                expiry = time.strftime('%H:%M:%S', time.localtime(result['expires_at']))
                self.lease_text.set_text('Projeto reservado por esta interface até ' + expiry + '. Renova antes de expirar.')
            self.message('Reserva atualizada.'); self.refresh(quiet=True)
        else:
            self.message('Pedido concluído. Atualiza ou verifica a prontidão da integração.')
            self.refresh(quiet=True)
        return False

    def apply_service_buttons(self):
        data = self.snapshot_data
        if not data or self.busy:
            return
        service = data['services']['bridge']
        active = service['active_state'] == 'active'
        unresolved = data['storage_status'] == 'unavailable' or data.get('restart_blockers', sum(data['counts'].get(key, 0) for key in ('accepted', 'running', 'unknown'))) > 0
        self.start_button.set_sensitive(bool(service['installed'] and data['profile_configured'] and not active))
        self.stop_button.set_sensitive(bool(service['installed'] and active and not unresolved))
        self.restart_button.set_sensitive(bool(service['installed'] and active and not unresolved))
        for button in (self.start_button, self.stop_button, self.restart_button):
            button.set_tooltip_text('O serviço próprio tem de estar instalado; trabalhos persistentes continuam; operações não persistentes ou incertas impedem a interrupção.')

    def render(self, data):
        self.snapshot_data = data
        service = data['services']['bridge']
        connected = data['connection'].get('remote_connected')
        connection = 'Não configurada' if not data['profile_configured'] else 'Túnel ligado' if connected is True else 'Sem ligação' if connected is False else 'Não verificada'
        self.values['connection'].set_text(connection)
        self.values['server'].set_text('Disponível' if data['launcher_available'] else 'Indisponível')
        active = sum(data['counts'].get(key, 0) for key in ('accepted', 'running'))
        self.values['active'].set_text('Indisponível' if data['storage_status'] == 'unavailable' else str(active))
        service_label = STATE_NAMES.get(service['active_state'], service['active_state']) if service['installed'] else 'estado indisponível' if service['installed'] is None else 'ainda não instalado'
        self.service_text.set_text('Serviço: ' + service_label + ' · Modo host · Sem Bubblewrap')
        self.profile_text.set_text('Perfil de túnel: ' + ('configurado' if data['profile_configured'] else 'ainda não configurado') + ' · Métricas na porta 8090')
        self.apply_service_buttons()
        for key, widget in self.integration_labels.items():
            state = data['services'][key]
            widget.set_text(('Serviço: ' + STATE_NAMES.get(state['active_state'], state['active_state']) if state['installed'] else 'Estado do serviço indisponível' if state['installed'] is None else 'Serviço não instalado') + ' · prontidão por verificar')
        clear(self.operation_list)
        dates = [r.get('updated_at', r.get('created_at', 0)) or 0 for r in data['receipts']]
        if dates:
            self.operation_list.append(label('Última atividade registada na bridge: ' + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(max(dates))) + ' · não comprova que o chat esteja ativo', 'muted small', wrap=True))
        if not data['receipts']:
            self.operation_list.append(label('Sem operações registadas.' if data['storage_status'] != 'unavailable' else 'Não foi possível ler os recibos.', 'muted'))
        for job in data.get('jobs', []):
            self.operation_list.append(label(f"Trabalho persistente {job['session_id'][:8]} · {STATE_NAMES.get(job['execution_state'], job['execution_state'])} · {job['cwd']}", 'muted small', wrap=True))
        for receipt in data['receipts']:
            row = box(spacing=5, style='section-row')
            top = box(False, 12)
            title = label(receipt['operation'], 'section-title'); title.set_hexpand(True); top.append(title)
            top.append(label('Resultado desconhecido' if receipt['state'] == 'unknown' else STATE_NAMES.get(receipt['state'], receipt['state']), 'caution' if receipt['state'] == 'unknown' else 'muted small'))
            row.append(top)
            detail = label(receipt['request_id'] + '\n' + receipt.get('path', receipt['scope']), 'muted small', wrap=True)
            detail.set_selectable(True); row.append(detail); self.operation_list.append(row)
        clear(self.reservation_list)
        if not data['leases']:
            self.reservation_list.append(label('Não há reservas ativas.' if data['storage_status'] != 'unavailable' else 'Não foi possível ler as reservas.', 'muted'))
        for lease in data['leases']:
            row = box(spacing=5, style='section-row')
            row.append(label(lease['resource'], 'section-title', wrap=True))
            expiry = time.strftime('%H:%M:%S', time.localtime(lease['expires_at']))
            row.append(label(lease['owner_id'] + ' · expira às ' + expiry, 'muted small', wrap=True))
            self.reservation_list.append(row)


def demo_snapshot():
    return {'ok': True, 'root': str(ROOT), 'profile_configured': False, 'launcher_available': True,
            'connection': {'remote_connected': None}, 'counts': {}, 'receipts': [], 'leases': [],
            'storage_status': 'empty', 'services': {key: {'installed': key != 'bridge',
            'active_state': 'active' if key == 'browser' else 'inactive'}
            for key in ('bridge', 'browser', 'unity', 'blender', 'godot', 'libresprite')}}


def panel_environment():
    """Pass session references and trusted config overrides, not unrelated API secrets."""
    allowed = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'DISPLAY', 'WAYLAND_DISPLAY',
        'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_SESSION_TYPE',
        'XDG_CURRENT_DESKTOP', 'SSH_AUTH_SOCK', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
        'XDG_DATA_HOME', 'JAVA_HOME', 'DOTNET_ROOT', 'BRIDGE_CONFIG', 'BRIDGE_OPERATIONS_DIR',
        'BRIDGE_MEMORY_DISABLED')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(BRIDGE_WORKSPACE=os.environ.get('BRIDGE_WORKSPACE', str(Path.home())),
               PYTHONDONTWRITEBYTECODE='1')
    return env


class Application(Adw.Application):
    def __init__(self, demo=False):
        super().__init__(application_id='io.github.local.BridgeControl.NOBUBBLEWARP',
                         flags=Gio.ApplicationFlags.NON_UNIQUE if demo else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.demo = demo

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            provider = Gtk.CssProvider(); provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            window = BridgeWindow(self, demo=self.demo)
        window.present()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='Preview without probing or changing host state')
    args = parser.parse_args()
    return Application(demo=args.preview).run([])


if __name__ == '__main__':
    raise SystemExit(main())
