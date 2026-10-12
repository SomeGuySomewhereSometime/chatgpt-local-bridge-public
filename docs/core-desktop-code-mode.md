# Core / Desktop e execução persistente — v0.10

## Perfis

`start.sh` inicia Core. `start-desktop.sh` inicia Desktop. Não existe perfil misto.
A seleção é feita antes do registo de ferramentas: uma ferramenta excluída não pode
ser chamada pelo MCP nem pelo Code Mode. Algumas consultas (contexto, recibos,
reservas, processos e integrações) são comuns aos dois perfis. Não são novas cópias
das bases de dados.

Core reúne ficheiros, Git, comandos, tarefas, memória, preparação de integrações e
Code Mode. Desktop reúne observação/input, aplicações e OBS. Os MCPs de Browser,
Blender, Unity, Godot e LibreSprite não são substituídos.

O Core continua a executar shell no host; separar catálogos não cria contenção de
sistema operativo. Configuração/caminhos aprovados continuam aplicáveis às ferramentas.

## Code Mode

A ferramenta `code_mode(code, request_id)` inicia um QuickJS 1.19.4 novo num processo
separado. Sem Node, imports, rede, filesystem ou timers no JavaScript. O controlador
encaminha chamadas para o mesmo dispatcher e schemas do Core.

```javascript
const results = tools.search({query: 'TODO', path: '.'});
text(results);
```

As chamadas `tools.nome({...})` são síncronas e ordenadas. `await` também funciona;
`Promise.all` não dá concorrência às ferramentas nesta versão. `text(value)` emite
resultados; variáveis intermédias não são automaticamente devolvidas. `ALL_TOOLS`
contém nomes, descrições curtas e schemas. Falhas MCP são erros JS; resultados de
negócio com `ok:false` continuam a exigir inspeção pelo código.

Limites: 64 KiB de código; 32 MiB no intérprete; 3 s de CPU do worker; 24 chamadas;
32 KiB de emissão; 2 MiB por resposta de ferramenta. O orçamento de 60 s é verificado
entre chamadas. Uma operação já despachada termina sob o seu timeout próprio antes
de o controlador concluir; matar o intérprete não desfaz alterações nem para jobs.

`request_id` é obrigatório e até 96 caracteres. Mutações recebem IDs determinísticos
`<id>.<índice>`. Repetir um ID de script não o executa de novo: devolve metadados dos
recibos, sem guardar conteúdos de ficheiros ou emissões. `task_begin`,
`task_checkpoint` e `memory_delete` são chamadas diretas; não podem ser aninhadas.
Consultar os recibos filhos depois de uma falha parcial; nunca repetir todo o script
com outro ID apenas para obter a resposta.

## Jobs persistentes

`run_command` usa uma unidade systemd independente por job. O worker fica responsável
pelo recibo antes de lançar o comando. O MCP guarda o session_id antes do despacho.
Reiniciar/parar o MCP ou fechar o chat não cancela o job. O comando
`command_session(command='stop', session_id=...)` para só a sua unidade, incluindo
descendentes. Servidores contínuos continuam a aceitar background=true, timeout=0;
o limite é 64 jobs ativos e a paragem é explícita.

Saída privada em `<operations>/jobs/<session_id>` com cursores em bytes, máximo de
4 MiB por stream. O restante é drenado e descartado; `output_truncated` assinala a
perda. Ficheiros UTF-8 são devolvidos sem duplicação de bytes entre páginas. Após
conclusão, outro processo MCP pode consultar a mesma sessão. Não existe retoma
automática de processos depois de reboot ou morte do worker: estado desconhecido
exige inspeção. O marcador `started.json` impede que reiniciar o worker repita o comando.

Retenção: na criação de novos jobs, resultados terminais com mais de sete dias são
removidos; resultados desconhecidos ficam preservados. Até 256 diretórios de jobs;
esgotar capacidade recusa novas execuções. Os recibos de deduplicação não são apagados.
A saída pode conter dados sensíveis do comando; é guardada em diretórios 0700 e
ficheiros 0600, fora do Git e das ferramentas genéricas de ficheiros. O pedido
transitório inclui comando/ambiente, e é eliminado após despacho confirmado.

As reservas são cooperativas e verificadas antes do despacho, como anteriormente;
não isolam shell arbitrário nem outros editores. Renovar reservas durante trabalho
longo. Gestão automática de vários agentes fica fora desta entrega.

O painel e watchdog permitem reiniciar Core com jobs persistentes em curso. Mutações
não persistentes, Code Mode ativo e resultados desconhecidos continuam a bloquear
a recuperação automática.

## Desktop partilhado

Um único serviço systemd por instalação mantém o helper, o consentimento e os IDs de
observação. Um socket Unix privado serializa pedidos; desligar um cliente MCP não
fecha a sessão de outro. As regras de expiração/consentimento GNOME continuam no
helper. Pedidos de input nunca são automaticamente repetidos após falha da ligação.

## Ativação

O túnel existente passa a Core, sem trocar o seu ID. Atualizar o catálogo do plugin
no ChatGPT. Para Desktop é necessária uma nova ligação:

1. Criar o túnel Desktop na mesma workspace e guardar a chave num ficheiro privado.
2. Copiar `ops/config/tunnel-client/chatgpt-local-bridge-desktop.yaml.example` para
   `~/.config/tunnel-client/chatgpt-local-bridge-desktop.yaml`, preencher o ID e
   confirmar o caminho da chave. Nunca colocar a chave no projeto ou no chat.
3. Instalar a unidade de `ops/units/mcp-tunnel-chatgpt-local-bridge-desktop.service`,
   fazer daemon-reload e iniciar manualmente.
4. Criar o plugin Desktop no ChatGPT a apontar para esse túnel.

A bridge com Bubblewrap permanece independente e desligada. Um rollback de código
não deve eliminar jobs em curso; primeiro consultar/parar apenas os jobs relevantes.
Os novos jobs não são visíveis em versões antigas. O backup de código não inclui
credenciais nem substitui cópias das bases privadas.
