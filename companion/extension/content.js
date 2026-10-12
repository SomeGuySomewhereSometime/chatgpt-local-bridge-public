(() => {
  const norm = s => (s || '').replace(/\s+/g,' ').trim();
  // Rendered text retains separators between ChatGPT's p/div/br nodes.
  const renderedText = e => e?.innerText ?? e?.textContent ?? '';
  // ChatGPT's rich composer may serialize an auto-linked bare URL as [URL](URL)
  // after Send. Accept only this lossless form, never a changed label or target.
  const messageText = s => norm(s).replace(/\[(https?:\/\/[^\s\[\]()]+)\]\(\1\)/g, '$1');
  // Reconstruct only the observed rich-text representation. Markdown links can
  // consume a JSON '[' as their opening delimiter; rendered text then loses it.
  function richSource(node) {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1) return '';
    if (node.tagName === 'BR') return '\n';
    const text = [...node.childNodes].map(richSource).join('');
    if (node.tagName === 'A') {
      const href = node.getAttribute('href');
      return /^https?:\/\//.test(href || '') ? '[' + text + '](' + href + ')' : text;
    }
    return /^(P|DIV|LI|PRE)$/.test(node.tagName) ? text + '\n' : text;
  }
  const documentId = crypto.randomUUID();
  let stopped = false, previousURL = location.href;
  const visible = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const editor = () => [...document.querySelectorAll('[contenteditable="true"][role="textbox"]')].find(visible);
  const buttons = () => [...(editor()?.closest('form')?.querySelectorAll('button') || [])].filter(visible);
  const named = (e,re) => re.test(e.getAttribute('aria-label') || e.textContent || '');
  // ChatGPT virtualizes history: mounted headings are not a message counter.
  const seenUsers = new Set();
  let usersURL = location.href;
  function users() {
    if (usersURL !== location.href) {seenUsers.clear(); usersURL = location.href;}
    const headings = [...document.querySelectorAll('h4')].filter(e => /^(O utilizador disse:|You said:)$/.test(norm(e.textContent)));
    let last = null, source = null, identified = true;
    for (const heading of headings) {
      const root = heading.parentElement;
      const id = root.querySelector('[data-chatgpt-search-message-ids]')?.getAttribute('data-chatgpt-search-message-ids');
      if (id) seenUsers.add(id); else identified = false;
      const parts = [...root.querySelectorAll('[data-user-message-bubble] .whitespace-pre-wrap')].filter(e => !e.parentElement?.closest('.whitespace-pre-wrap'));
      source = parts.length ? norm(parts.map(e => e.querySelector?.('[data-markdown-text-tone="user-message"]') ? richSource(e) : renderedText(e)).join('\n')) : null;
      last = parts.length ? norm(parts.map(renderedText).join('\n')) : null;
    }
    return {count: seenUsers.size, last, source, identified, root:headings.at(-1)?.parentElement, ids:[...seenUsers], last_id: headings.at(-1)?.parentElement.querySelector('[data-chatgpt-search-message-ids]')?.getAttribute('data-chatgpt-search-message-ids') || ''};
  }
  function state() {
    if(previousURL !== location.href) {stopped=false; previousURL=location.href;}
    const statuses=[...document.querySelectorAll('[role="status"],[role="alert"]')].map(e=>norm(e.textContent)).join(' ');
    const stop=buttons().some(e=>named(e,/^(Parar|Stop|Stop generating)$/i));
    if([...document.querySelectorAll('[role="dialog"] button')].some(e=>named(e,/aprovar|permitir|confirmar|approve|allow|confirm/i))) return 'approval';
    if(stop) return 'generating';
    if(/something went wrong|erro ao|network error|falha de ligação|unable to load/i.test(statuses)) return 'error';
    if(!editor()) return 'unknown';
    if(norm(renderedText(editor()))) return 'draft';
    if(stopped) return 'interrupted';
    if(/Resposta concluída|Response complete/i.test(statuses)) return 'completed';
    // Reloaded history has no completion announcement. Require the last turn's
    // explicit terminal marker AND its response actions, not just absence of Stop.
    const turns = [...document.querySelectorAll('[data-talvt-turn-state]')];
    const last = turns.at(-1);
    if (last?.getAttribute('data-talvt-turn-state') === 'complete' &&
        [...last.querySelectorAll('h4')].some(e => /^(O ChatGPT disse:|ChatGPT said:)$/.test(norm(e.textContent))) &&
        [...last.querySelectorAll('button')].some(e => visible(e) && named(e,/^(Gerar resposta novamente|Regenerate response|Try again)$/i)) &&
        !last.querySelector('[aria-busy="true"]')) return 'idle';
    return 'unknown';
  }
  function snapshot() {
    const last = [...document.querySelectorAll('[data-talvt-turn-state]')].at(-1);
    const assistant = last && [...last.querySelectorAll('h4')].filter(e => /^(O ChatGPT disse:|ChatGPT said:)$/.test(norm(e.textContent))).at(-1);
    let marker = null;
    if (assistant) {
      const clone = assistant.parentElement.cloneNode(true);
      clone.querySelectorAll('button,h4').forEach(e => e.remove());
      const text = clone.textContent.trim();
      const matches = [...text.matchAll(/\[BRIDGE_LOOP:([a-f0-9]{32}):(PENDING|DONE|NEEDS_USER)\]/g)];
      const final = text.match(/\[BRIDGE_LOOP:([a-f0-9]{32}):(PENDING|DONE|NEEDS_USER)\]\s*$/);
      if (matches.length === 1 && final) marker = {nonce:final[1],decision:final[2]};
    }
    let current = state();
    if (norm(renderedText(editor()))) current = 'draft';
    const messages = users();
    if (!messages.identified) current = 'unknown';
    return {url:location.href,state:current,document_id:documentId,user_count:messages.count,last_user_id:messages.last_id,last_user_text:messages.source || messages.last || '',
      terminal:!!assistant && last?.getAttribute('data-talvt-turn-state') === 'complete' &&
        !last.querySelector('[aria-busy="true"]') && ['idle','completed'].includes(current),marker};
  }
  // ChatGPT truncates a collapsed long bubble to 10,240 characters. Expand
  // only the identified message being checked; never use a prefix as proof.
  async function expandMessage(expected, url, identity, attempted) {
    const current=users();
    if (!current.identified || !identity || current.last_id!==identity || attempted.has(identity)) return;
    if ([current.last,current.source].some(text=>messageText(text)===messageText(expected))) return;
    const more=[...(current.root?.querySelectorAll('button')||[])].find(e=>visible(e)&&!e.disabled&&named(e,/^(Mostrar mais|Show more|Read more)$/i));
    if (!more) return;
    requireContext();
    if (location.href!==url) throw Error('Conversation changed while reading the message.');
    attempted.add(identity); more.click();
    await new Promise(r=>setTimeout(r,100));
    requireContext();
    if(location.href!==url || users().last_id!==identity) throw Error('Conversation changed while expanding the message.');
  }
  let active = true, observing = false, heartbeat;
  function shutdown() {
    if (!active) return;
    active = false;
    clearInterval(heartbeat);
    document.removeEventListener('click', onClick, true);
    try { chrome.runtime.onMessage.removeListener(onMessage); } catch (_) {}
  }
  function contextAlive() {
    try { if (active && chrome.runtime.id) return true; } catch (_) {}
    shutdown(); return false;
  }
  function requireContext() {
    if (!contextAlive()) throw Error('Extension reloaded. Reload this ChatGPT page before sending again.');
  }
  function onClick(e) {
    if (!contextAlive()) return;
    const b=e.target.closest('button');
    if(b&&named(b,/^(Parar|Stop|Stop generating)$/i)) stopped=true;
  }
  async function observe() {
    if (observing || !contextAlive()) return;
    observing = true;
    try {
      await chrome.runtime.sendMessage({kind:'native',payload:{action:'observe',state:state(),snapshot:(()=>{const s=snapshot();delete s.last_user_text;delete s.last_user_id;return s;})()}});
    } catch (e) {
      // sendMessage may throw synchronously after an extension reload.
      if (/context invalidated/i.test(String(e?.message || e)) || !contextAlive()) shutdown();
    } finally { observing = false; }
  }
  document.addEventListener('click', onClick, true);
  heartbeat = setInterval(observe,5000);
  function onMessage(msg,sender,reply) {
    if(!contextAlive() || sender.id!==chrome.runtime.id) return;
    if(msg.kind==='inspect') {reply({...snapshot(),draft:!!norm(renderedText(editor()))});return;}
    if(msg.kind==='verify_delivery') {
      (async()=>{
        requireContext();
        if(location.href!==msg.url || typeof msg.text!=='string' || msg.text.length>100000)throw Error('Conversation changed or receipt text is unavailable.');
        const before=users();
        if(!before.identified || !before.last_id || (msg.message_id && before.last_id!==msg.message_id))throw Error('The latest message identity does not match this receipt.');
        await expandMessage(msg.text,msg.url,before.last_id,new Set());
        const now=users();
        if(!now.identified || before.last_id!==now.last_id || ![now.last,now.source].some(text=>messageText(text)===messageText(msg.text)))throw Error('The complete latest message does not match this receipt. Nothing was sent.');
        const checked=snapshot();
        return {ok:true,snapshot:{...checked,last_user_text:now.source||now.last},verified:true};
      })().then(reply,e=>reply({ok:false,error:e.message}));
      return true;
    }
    if(msg.kind!=='send') return;
    let observation=null;
    const expanded=new Set();
    const started=Date.now(), diagnosticMeta={clicked:false,text_length:msg.text?.length||0,polls:0};
    (async()=>{
      requireContext();
      if(location.href!==msg.url || !['idle','completed','interrupted'].includes(state())) throw Error('The conversation changed, is busy, or its state is unconfirmed.');
      const el=editor(), initial=users(), before=initial.count;
      if (!initial.identified) throw Error('Message identities could not be verified. Reload the page before trying again.');
      if (msg.loop && (msg.document_id !== documentId || before !== msg.user_count)) throw Error('Loop page or conversation changed.');
      if(!el || norm(renderedText(el))) throw Error('Your ChatGPT draft was preserved. Clear or send it yourself before continuing.');
      el.focus();
      if(!document.execCommand('insertText',false,msg.text)) throw Error('Could not insert the message.');
      await new Promise(r=>setTimeout(r,200));
      requireContext();
      if(location.href!==msg.url || norm(renderedText(el))!==norm(msg.text)) throw Error('Message text could not be verified. Check the ChatGPT draft.');
      const send=buttons().find(e=>named(e,/^(Enviar|Send|Send message)$/i));
      if(!send || send.disabled || buttons().some(e=>named(e,/^(Parar|Stop|Stop generating)$/i))) throw Error('Send is unavailable. The draft was preserved.');
      if (msg.loop) {
        const permission = await chrome.runtime.sendMessage({kind:'loop_validate',id:msg.id});
        if (!permission?.allowed || location.href!==msg.url || (!users().identified || users().count!==before) || norm(renderedText(el))!==norm(msg.text)) throw Error('Loop stopped or page changed. Check the draft; nothing was clicked.');
      }
      requireContext();
      stopped=false; send.click(); diagnosticMeta.clicked=true;
      const deadline=Date.now()+10000;
      let diagnostic = 'No matching new message was observed.';
      for(let i=0;i<40 && Date.now()<deadline;i++) {
        await new Promise(r=>setTimeout(r,250));
        requireContext();
        if(location.href!==msg.url) throw Error('The conversation changed after the attempt.');
        let now=users();
        const newMessage=now.identified && now.count===before+1 && now.last_id && !initial.ids.includes(now.last_id);
        if(newMessage) {
          observation={document_id:documentId,last_user_id:now.last_id,user_count:now.count};
          await expandMessage(msg.text,msg.url,now.last_id,expanded);
          now=users();
        }
        Object.assign(diagnosticMeta,{polls:i+1,bubble_expanded:expanded.size>0,observed_text_length:(now.source||now.last||'').length,identified:!!now.identified,count_matches:now.count===before+1,text_matches:[now.last,now.source].some(text=>messageText(text)===messageText(msg.text))});
        if(now.identified && now.count===before+1 && now.last_id && !initial.ids.includes(now.last_id) && [now.last,now.source].some(text => messageText(text)===messageText(msg.text))) return {ok:true,state:'observed_sent',observation,diagnostic_meta:{...diagnosticMeta,elapsed_ms:Date.now()-started}};
        diagnostic = !now.identified ? 'Message identity unavailable.' : now.count!==before+1 ? 'New message count did not match.' : 'New message found, but its text did not match.';
      }
      throw Error('Delivery could not be confirmed. '+diagnostic+' Check the conversation before starting another request.');
    })().then(reply,e=>reply({ok:false,state:'uncertain',error:e.message,observation,diagnostic_meta:{...diagnosticMeta,elapsed_ms:Date.now()-started}}));
    return true;
  }
  chrome.runtime.onMessage.addListener(onMessage);
})();
