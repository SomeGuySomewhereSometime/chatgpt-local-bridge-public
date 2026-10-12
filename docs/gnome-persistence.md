# Autorizações GNOME/Wayland persistentes (NOBUBBLEWARP)

`desktop_session(start)` consulta a versão do portal RemoteDesktop. Com versão 2
ou superior pede `persist_mode=2` em `SelectDevices`, incluindo o token guardado
quando existe. `ScreenCast.SelectSources` mantém um monitor e cursor integrado,
sem opções de persistência: as sessões combinadas usam apenas RemoteDesktop.
O GNOME apresenta o consentimento quando necessário. Nenhuma ação substitui a
decisão do portal, pede root ou altera o PermissionStore do GNOME.

Depois de autorizar teclado, ponteiro e um monitor, o token novo de `Start` é
gravado automaticamente. O token anterior é de uso único: é retirado do disco
antes da tentativa e nunca repetido após cancelamento, timeout ou resultado
incerto. Uma falha nesse intervalo pode exigir novo consentimento. O token só
é guardado se a sessão tiver os dispositivos e geometria de monitor exigidos.

O diretório fixo do utilizador é
`~/.local/state/chatgpt-local-bridge-NOBUBBLEWARP/desktop-portal` (0700), com
`remote-desktop.json` e `grant.lock` (0600). A gravação usa temporário privado,
fsync e substituição atómica. Componentes symlink, ficheiros públicos, hardlinks,
proprietário incorreto, conteúdo inválido e ficheiros demasiado grandes são
recusados. Um lock não bloqueante cobre a tentativa até ao retorno de `Start`:
dois workers não podem consumir o mesmo token. Tokens não aparecem no IPC,
estado MCP, imagens ou erros. O diretório é protegido dos file tools da bridge;
o shell NOBUBBLEWARP continua a ter os acessos normais do utilizador.

Sem suporte, armazenamento seguro ou token substituto, a sessão autorizada
continua a funcionar normalmente. Uma recusa explícita da persistência em
SelectDevices permite uma única tentativa nova transitória; cancelamentos do
utilizador nunca são repetidos. Tokens inválidos ou permissões revogadas são
tratados pelo portal, que pode mostrar o diálogo normal.

`desktop_session(status).persistence` informa versão, suporte da API, modo
pedido, tentativa de restauro, existência do token, gravação do grant e aviso,
sem revelar o segredo. `stop` fecha a sessão mas conserva o grant guardado.
Também se mantém o encerramento após 15 minutos sem atividade. O arranque do
worker e a consulta de estado não restauram sessões automaticamente: o restauro
é feito ao pedir `start`; não se inicia controlo remoto só porque o PC arrancou.

`screen_capture` reutiliza a sessão RemoteDesktop **já ativa** para pedidos não
interativos com cursor. A resposta indica `capture_scope=selected-monitor` e o
ID da sessão. Não inicia sessões nem pede autorização de input. Pedidos
interativos, sem cursor, sem sessão ativa ou com falha PipeWire usam as vias de
captura existentes. `desktop_observe(screenshot=true)` usa a mesma sessão.

## Compatibilidade e reinício do PC

Ambiente verificado: GNOME Shell 50.1, xdg-desktop-portal-gnome
50.0-0ubuntu1, xdg-desktop-portal 1.21.1+ds-1ubuntu3.1 e RemoteDesktop v2.
O código oficial GNOME 50.0 processa o modo persistente e dados de restauro,
mas só devolve dados persistentes quando `shared.persist_allowed` está ativo.
O modo 2 é um pedido, não uma garantia de concessão do backend.

Os tokens são guardados fora de `/tmp` e sobrevivem ao reinício. Depois do login
na sessão GNOME/Wayland, `start` tenta restaurar a autorização. Pode ser necessário
autorizar novamente se o GNOME não conceder persistência, revogar a permissão,
não aceitar o token, mudar o monitor/identidade da aplicação ou perder o token.
Esta funcionalidade não dá acesso antes do login nem à sessão bloqueada/GDM.
Um teste com dois workers valida reinício do processo; não prova reinício do PC.

Referências primárias:

- [RemoteDesktop: persistência e tokens de uso único](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
- [Código oficial GNOME 50.0](https://download.gnome.org/sources/xdg-desktop-portal-gnome/50/xdg-desktop-portal-gnome-50.0.tar.xz) (`src/remotedesktop.c`, `handle_select_devices`, `start_done`, `restore_from_data`).

## Verificação

Sem consentimento nem dados de produção:

```bash
cd /home/bridge-user/chatgpt-local-bridge-NOBUBBLEWARP
BRIDGE_SYSTEMD_TESTS=1 ./check.sh
```

Teste real opcional, sem enviar rato/teclado, com o diálogo de consentimento
nativo quando necessário; guarda o grant normal, mas não imprime tokens/imagens:

```bash
cd /home/bridge-user/chatgpt-local-bridge-NOBUBBLEWARP
.venv/bin/python -B check_portal_persistence.py --live
```

Esse teste fecha apenas as suas duas sessões descartáveis; conserva o grant.
Para esquecer o token local, fechar as sessões desta bridge e remover apenas o
ficheiro `remote-desktop.json` do diretório indicado. Isto não modifica as
permissões guardadas pelo GNOME; o próximo `start` pede consentimento normal.

## Resultado desta instalação (2026-10-07)

- Suite instalada: 207 testes, 5 exclusões esperadas; 44 testes GI passaram.
- 29 novas regressões: armazenamento/rotação/segurança, protocolo e captura.
- Consentimento real GNOME: modo 2 concedido e token privado guardado.
- Segundo worker: restauro pedido com token e grant substituto guardado.
- Serviço atualizado: restauro e rotação confirmados também pela integração.
- Captura real via screen_capture: PipeWire, monitor selecionado, PNG 1920x1080,
  mesma session_id; sessão de teste encerrada e imagem temporária removida.
- Diretório 0700 e ficheiros 0600, pertencentes ao utilizador, verificados.
- Bridge original permaneceu parada/intacta. Reboot do PC não executado.
- Backup: /home/bridge-user/Documentos/ChatGPT/fix/backups/NOBUBBLEWARP-20261007T221554Z.tar.gz.
