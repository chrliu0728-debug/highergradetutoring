// The message thread widget. Both the customer portal and the staff dashboard
// mount this; they differ only in how they fetch/send and whose bubbles are
// drawn on the right-hand side.

import { el, timeAgo } from './site.js';

export function mountChat(container, options) {
  const {
    load,                 // async (sinceId) => { messages, status, confirmedSlot }
    send,                 // async (body) => void
    mineIs = 'customer',  // which author_type renders as "my" bubble
    pollMs = 4000,
    onMeta = () => {},
    placeholder = 'Write a message…',
  } = options;

  container.classList.add('chat');
  container.innerHTML = '';

  const log = el('div', { class: 'chat-log' });
  const input = el('textarea', { placeholder, rows: '1' });
  const button = el('button', { class: 'btn', type: 'submit', text: 'Send' });
  const form = el('form', { class: 'chat-form' }, [input, button]);
  container.append(log, form);

  let lastId = 0;
  let timer = null;
  let stopped = false;

  function render(messages) {
    let appended = false;
    for (const message of messages) {
      if (message.id <= lastId) continue;
      lastId = message.id;
      appended = true;

      const isSystem = message.authorType === 'system';
      const isMine = message.authorType === mineIs;
      const via = message.source === 'discord' ? ' · via Discord' : '';
      const bubble = el('div', { class: `bubble${isSystem ? ' system' : isMine ? ' mine' : ''}` }, [
        isSystem
          ? null
          : el('span', { class: 'who', text: `${message.authorName} · ${timeAgo(message.createdAt)}${via}` }),
        message.body,
      ]);
      log.append(bubble);
    }
    if (appended) log.scrollTop = log.scrollHeight;
  }

  async function poll() {
    if (stopped) return;
    try {
      const data = await load(lastId);
      render(data.messages || []);
      onMeta(data);
    } catch (err) {
      console.error('[chat] poll failed:', err);
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    button.disabled = true;
    input.disabled = true;
    try {
      await send(body);
      input.value = '';
      await poll();
    } catch (err) {
      log.append(el('div', { class: 'bubble system', text: `Could not send: ${err.message}` }));
      log.scrollTop = log.scrollHeight;
    } finally {
      button.disabled = false;
      input.disabled = false;
      input.focus();
    }
  });

  // Enter sends, shift+enter makes a new line.
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  poll();
  timer = setInterval(poll, pollMs);

  return {
    refresh: poll,
    destroy() {
      stopped = true;
      clearInterval(timer);
    },
  };
}
