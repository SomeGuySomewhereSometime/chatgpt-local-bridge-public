# Bridge Control NOBUBBLEWARP

Painel nativo GTK4/Adwaita, independente da interface/atalho da bridge original.

```bash
/home/bridge-user/chatgpt-local-bridge-NOBUBBLEWARP/interface/start.sh
```

Também aparece no menu de aplicações como **Bridge Control NOBUBBLEWARP** quando o ficheiro `.desktop` desta pasta estiver instalado em `~/.local/share/applications/`.

## Páginas

- Visão geral: estado do serviço próprio, configuração do túnel, diagnóstico MCP local e operações ativas.
- Integrações: verificar ou preparar Browser, Unity, Blender, Godot e LibreSprite. Estes serviços são partilhados com a instalação original. Preparar apenas inicia serviços instalados inativos; não abre aplicações.
- Projetos: escolher uma pasta, consultar/preparar caches e criar explicitamente um venv persistente sem instalar dependências.
- Operações: últimos 40 recibos, com estado e metadados. Nunca repete automaticamente uma operação incerta.
- Reservas: lista de reservas ativas sem tokens. Na página Projetos é possível reservar um projeto por 5 minutos, renovar e libertar a reserva desta interface.
- Ligação e ajuda: configuração necessária para o túnel separado e acesso à pasta/documentação.

O painel não instala túneis, altera credenciais ou atualiza plugins automaticamente. Os botões da ligação estão desativados enquanto o serviço/perfil próprios não estiverem instalados. O backend também recusa interrupções quando há trabalho ativo, recibos desconhecidos ou armazenamento indisponível.

Abrir a interface apenas observa o estado. As ações acontecem ao clicar nos respetivos botões. As leituras de recibos/reservas usam SQLite em modo de leitura, sem criar/migrar a base de dados. O diagnóstico inicia uma instância MCP temporária e consulta o catálogo; não executa comandos no host e não comprova a ligação hospedada.

As ações de projeto/preparação usam a política, reservas e recibos existentes da variante. Cada pedido de mutação recebe um ID antes do envio. Reservas não bloqueiam editores externos, outros MCPs ou shell livre. Ao fechar a janela, as reservas expiram naturalmente; não há libertação ou preparação oculta.

## Pré-visualização e testes

GTK/Adwaita usam o Python do sistema e os pacotes GI já instalados. O backend usa o ambiente `.venv` desta bridge. Não foi acrescentada uma dependência Python ao ambiente da bridge.

```bash
interface/start.sh --preview
/usr/bin/python3 -B interface/check_ui.py
/usr/bin/python3 -B interface/check_ui_actions.py
.venv/bin/python -B -m unittest -q test_control_interface
```

`--preview` mostra estados de exemplo e não consulta nem altera os serviços. `check_ui.py` abre uma janela de teste e renderiza apenas essa janela, sem capturar o desktop. `check_ui_actions.py` testa callbacks reais, caches e reservas em pastas temporárias; não prepara serviços nem abre editores. Os testes nativos precisam de uma sessão gráfica.

O atalho antigo encontrado apontava para uma pasta inexistente (`ChatGptPCbridge/BridgeControl`). Como o seu código/visual não estavam disponíveis, este painel recria o propósito de controlo dos MCPs com uma interface nativa própria.

## Continuidade

Usa a pasta selecionada em Projetos. A página Continuidade lista tarefas e mostra
checkpoints históricos com estado declarado, revisão, validações e próximo passo.
Pode copiar contexto para colar manualmente noutro turno ou exportar Markdown para
um ficheiro novo. Nunca retoma chats, executa o próximo passo ou substitui um documento
existente silenciosamente. Leituras não criam nem migram a base de dados.

Teste com dados temporários: `/usr/bin/python3 -B interface/check_continuity_ui.py`.
