# Continuidade e chamadas demoradas — NOBUBBLEWARP

A bridge conserva pontos de situação explícitos por tarefa na base de memória já configurada. O registo permite recuperar objetivo, trabalho realizado, verificações, limitações e próximo passo. Não conserva automaticamente o chat, raciocínio privado, comandos ou saídas. Não resolve nem controla o estado do botão Stop no ChatGPT.

## Trabalhar por tarefa

1. Chamar `get_session_context(cwd=<raiz explícita>)`. Consultar `continuity.tasks` e escolher a tarefa relevante. Havendo várias, não presumir que a última pertence ao chat atual.
2. Para trabalho novo, chamar `task_begin` com título, objetivo, restrições, origem e um `request_id` estável. Guardar o `task_id` e a revisão devolvidos.
3. Guardar `task_checkpoint` antes de operações demoradas, após etapas relevantes e validação, e antes de encerrar o turno. Fornecer a revisão esperada e um novo `request_id` por atualização. Após perda de resposta, repetir apenas o mesmo pedido, com os mesmos parâmetros e ID.
4. Ao retomar, ler `task_context` e verificar o estado real dos ficheiros, aplicações e operações referidas. O próximo passo guardado é contexto histórico, não uma ordem nem autorização adicional.

Exemplo de payload para `task_checkpoint`:

```json
{
  "summary": "Câmara ajustada; falta validar alcance",
  "completed": ["Campo de batalha ampliado"],
  "files": [{"path": "src/main.ts", "version": "hash ou versão observada"}],
  "validation": [{"description": "Compilação", "status": "passed", "evidence": "Verificação observada pelo agente"}],
  "unverified": ["Disparos entre os extremos"],
  "pending": [],
  "next_step": "Executar o teste de disparos à nova distância",
  "recovery": ["Versão anterior preservada na pasta backups"]
}
```

`validation.status` aceita `passed`, `failed`, `not_run` e `incomplete`. Os estados da tarefa são `in_progress`, `paused`, `blocked` e `completed`; são declarados pelo agente, não telemetria do ChatGPT. Tarefas não concluídas exigem um próximo passo. `completed` recusa trabalho pendente/por validar e validações não aprovadas. A ferramenta verifica também se há comandos ligados ativos ou de resultado incerto. Isto reduz conclusões prematuras, mas não prova que a tarefa satisfez realmente o utilizador.

As revisões são imutáveis. `expected_revision` impede substituição silenciosa; `request_id` evita duplicações. `task_context(revision=N)` permite consultar qualquer revisão existente. As listas e o histórico são limitados e indicam quando há mais resultados; a consulta por tarefa/revisão devolve o checkpoint completo.

## Comandos com resposta curta

`run_command` usa agora sessões geridas também para comandos normais. Se terminar durante a espera inicial, devolve o resultado. Se continuar, devolve `running=true`, `promoted_to_background=true` e `session_id`. Não lança um segundo processo.

- `yield_after`: espera inicial **após o arranque**, entre 0 e 2 segundos; por defeito 1. O arranque da unidade systemd tem os seus próprios tempos limite e pode demorar mais.
- `timeout`: duração permitida da execução. Continua limitado a 1..300 segundos para chamadas normais. `background=true, timeout=0` mantém o funcionamento existente para servidores até serem parados explicitamente.
- Saída inicial até 16000 caracteres por stream. `output_pending`, `stdout_more` e `stderr_more` indicam que falta ler saída, mesmo se a execução já terminou. Continuar a partir dos cursores; não executar de novo.
- `command_session`: consultar com os cursores `stdout_offset`/`stderr_offset`, evitando repetir saída; espera máxima de 10 segundos por consulta. A saída é persistida (4 MiB por stream); os cursores representam bytes. Pode ser consultada após reiniciar o MCP.
- `execution_state`: `running`, `completed`, `failed`, `timed_out`, `cancelled` ou `unknown`. `automatic_retry=false` é explícito. `returncode=0` não equivale a validação do objetivo.
- `task_id`: liga apenas metadados do comando à tarefa na raiz de projeto selecionada. Usar `project_root` ao executar numa subpasta. Comandos, stdout e stderr não entram no checkpoint.

Antes de enviar, escolher um `request_id`. Após uma resposta perdida, consultar `command_request` com o mesmo ID e cwd; nunca lançar uma cópia para recuperar a resposta. Jobs persistentes permitem reiniciar o MCP; mutações não persistentes e resultados incertos continuam a proteger a recuperação.

Antes de parar um chat, consultar o seu trabalho e guardar progresso quando possível. O botão Stop do ChatGPT não é um mecanismo garantido para parar um comando da bridge. Os jobs pertencem a unidades systemd independentes: um reinício do MCP preserva-os. Parar o job exige command_session(stop). Reboot ou morte do worker não relançam o comando. Aplicações abertas por `app_launch` mantêm o ciclo de vida separado.

## Interface e exportação

A página **Continuidade** usa a pasta selecionada em **Projetos**. Lista tarefas, mostra revisão, estado declarado, data e conteúdo completo. “Copiar para continuar” prepara contexto para colar manualmente no chat. Não envia mensagens nem executa o próximo passo.

“Exportar Markdown” grava uma cópia identificada em data/revisão. Usa a política de ficheiros, os recibos, as reservas e a escrita atómica da bridge. Só cria um ficheiro novo: não substitui um `PROGRESSO.md` existente silenciosamente. A base de dados é a fonte principal; a exportação é histórica. A leitura no painel não cria nem migra bases de dados. As operações ligadas que não estão nos últimos recibos visíveis ficam explicitamente `not_checked`.

Em **Operações**, a data da última atividade registada é independente do estado do chat. Ausência de atividade não significa bloqueio: o agente pode estar a pensar ou a usar outra ferramenta.

## Persistência, privacidade e limites

- Tabelas de continuidade versionadas na base SQLite existente, sem substituir notas ou histórico e sem alterar a versão do esquema legado. São criadas transacionalmente na primeira escrita explícita. Clientes antigos continuam a usar as tabelas existentes.
- Diretório privado 0700 e base 0600, transações SQLite e restrições de âmbito por raiz exata. A memória desligada continua desligada.
- Checkpoints até 16000 caracteres, listas limitadas, até 200 tarefas por projeto e capacidade de revisões limitada pela configuração existente. Não há remoção automática de revisões para libertar espaço.
- Texto passa pela remoção existente de formatos comuns de credenciais, que é uma mitigação de melhor esforço. Não guardar segredos. Não há cópia automática de logs, ficheiros, conversas ou outputs.
- Se a gravação falhar, o trabalho anterior pode já ter sido executado. Consultar efeitos e recibos; não repetir a ação para preencher o histórico.
- A bridge só conhece chamadas que recebe. Trabalho feito por ferramentas próprias do Codex, Unity/Blender MCP ou outras integrações exige checkpoints explícitos do agente.
- Uma interrupção abrupta preserva apenas o último registo confirmado. Não existe garantia de gravar um resumo final depois de Stop, nem prevenção garantida de falhas da interface do ChatGPT.

## Verificar uma instalação

```bash
./check.sh
.venv/bin/python -B check_continuity_mcp.py
/usr/bin/python3 -B interface/check_continuity_ui.py
```

Os testes usam configurações e bases descartáveis. O teste MCP cria dois processos temporários e uma sessão inofensiva; não reinicia o túnel em utilização nem trabalha em projetos reais.


## Version-bound checks

The existing evidence text may include [validation:ID]. run_command(validation_paths=[...]) captures monitored source versions around execution in its independent worker. validation_evidence begin/finish handles browser or manual checks, whose outcomes remain reported. Explicit stale, unfinished, unavailable or failed references prevent a new completed checkpoint; history is retained. Task context and Workspace expose freshness, recorded scopes and linked command receipt status. Ordinary legacy evidence keeps its existing semantics. Matching source bytes do not prove semantic coverage or aesthetic quality.
