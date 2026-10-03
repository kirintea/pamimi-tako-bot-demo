// -*- coding: utf-8 -*-
/**
 * AgentScope Chat 前端逻辑 — v2 浅色主题（参考 WebUI）
 */

// ============ DOM Elements ============
var messagesEl, messagesAreaEl, heroAreaEl, inputEl, sendBtn, stopBtn;
var userDisplayEl, sessionListEl, sidebarEl, btnCollapse;
var sessionSearchEl, modelNameEl;

// ============ State ============
var currentUserId = localStorage.getItem('user_id') || 'user_001';
var currentSessionId = '';
var isStreaming = false;
var currentAbortController = null;
var hasMoreMessages = false;
var oldestMessageId = null;
var allSessions = [];
var renderPending = false;

// ============ Markdown Setup ============
function setupMarked() {
  if (typeof marked === 'undefined') return;
  try {
    marked.setOptions({
      breaks: true,
      gfm: true,
    });
  } catch (e) {
    console.warn('marked.setOptions failed:', e);
  }
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    try {
      return marked.parse(text);
    } catch (e) {
      console.warn('marked.parse failed:', e);
    }
  }
  return escapeHtml(text);
}

/** 对已渲染的 DOM 中的 <pre><code> 块应用 highlight.js */
function highlightCodeBlocks(container) {
  if (typeof hljs === 'undefined') return;
  var blocks = container.querySelectorAll('pre code');
  for (var i = 0; i < blocks.length; i++) {
    if (!blocks[i].dataset.highlighted) {
      try { hljs.highlightElement(blocks[i]); } catch (e) { /* ignore */ }
    }
  }
}

// ============ Init ============
function init() {
  // Cache DOM refs
  messagesEl = document.getElementById('messages');
  messagesAreaEl = document.getElementById('messagesArea');
  heroAreaEl = document.getElementById('heroArea');
  inputEl = document.getElementById('input');
  sendBtn = document.getElementById('sendBtn');
  stopBtn = document.getElementById('stopBtn');
  userDisplayEl = document.getElementById('userDisplay');
  sessionListEl = document.getElementById('sessionList');
  sidebarEl = document.getElementById('sidebar');
  btnCollapse = document.getElementById('btnCollapse');
  sessionSearchEl = document.getElementById('sessionSearch');
  modelNameEl = document.getElementById('modelName');

  setupMarked();
  userDisplayEl.textContent = currentUserId;
  updateModelName();
  loadSessionList();
  setupComposerEvents();

  // Hero greeting random
  var greetings = ['今天想完成什么？', '有什么可以帮你的？', '开始一个新的对话', '告诉我你的想法'];
  document.getElementById('heroGreeting').textContent = greetings[Math.floor(Math.random() * greetings.length)];
}

function updateModelName() {
  var name = localStorage.getItem('llm_model_name') || 'GLM-5';
  modelNameEl.textContent = name;
}

// ============ Sidebar ============
function toggleSidebar() {
  sidebarEl.classList.toggle('collapsed');
  btnCollapse.textContent = sidebarEl.classList.contains('collapsed') ? '»' : '«';
}

// ============ Session List ============
function loadSessionList() {
  fetch('/sessions/' + encodeURIComponent(currentUserId))
    .then(function (resp) { return resp.json(); })
    .then(function (data) {
      allSessions = data.sessions || [];
      renderSessionList(allSessions);
    })
    .catch(function (err) {
      console.error('加载会话列表失败:', err);
    });
}

function renderSessionList(sessions) {
  if (!sessions.length) {
    var emptyMsg = (sessionSearchEl && sessionSearchEl.value.trim()) ? '无匹配会话' : '暂无会话';
    sessionListEl.innerHTML = '<div class="session-list-empty">' + emptyMsg + '</div>';
    return;
  }

  var html = '';
  for (var i = 0; i < sessions.length; i++) {
    var s = sessions[i];
    var isActive = s.session_id === currentSessionId;
    var title = escapeHtml(s.title || s.session_id.slice(0, 8));
    html += '<div class="session-item ' + (isActive ? 'active' : '') + '"' +
      ' onclick="switchSession(\'' + currentUserId + '\',\'' + s.session_id + '\')"' +
      ' title="' + escapeHtml(s.session_id) + '">' +
      '<span class="session-item-title">' + title + '</span>' +
      '<span class="session-item-actions">' +
      '<button onclick="event.stopPropagation();showRenameDialog(\'' + s.session_id + '\',\'' + escapeHtml(s.title || '') + '\')" title="重命名">✏️</button>' +
      '<button class="btn-sess-delete" onclick="event.stopPropagation();showDeleteDialog(\'' + s.session_id + '\',\'' + escapeHtml(s.title || '') + '\')" title="删除">✕</button>' +
      '</span></div>';
  }
  sessionListEl.innerHTML = html;
}

function filterSessions(query) {
  var q = query.trim().toLowerCase();
  if (!q) {
    renderSessionList(allSessions);
    return;
  }
  var filtered = [];
  for (var i = 0; i < allSessions.length; i++) {
    var s = allSessions[i];
    if ((s.title || '').toLowerCase().indexOf(q) !== -1 ||
      s.session_id.toLowerCase().indexOf(q) !== -1) {
      filtered.push(s);
    }
  }
  renderSessionList(filtered);
}

// ============ Session Switch ============
function switchSession(userId, sessionId) {
  if (isStreaming) return;
  if (sessionId === currentSessionId) return;

  currentSessionId = sessionId;
  messagesEl.innerHTML = '';
  hasMoreMessages = false;
  oldestMessageId = null;

  showMessagesView();

  // Load history
  fetch('/sessions/' + encodeURIComponent(userId) + '/' + encodeURIComponent(sessionId) + '/messages?limit=50')
    .then(function (resp) {
      if (!resp.ok) {
        return resp.json().catch(function () { return {}; }).then(function (errBody) {
          addMessage('error', '加载会话失败 (' + resp.status + '): ' + (errBody.detail || resp.statusText));
          return null;
        });
      }
      return resp.json();
    })
    .then(function (data) {
      if (!data) return;
      var messages = data.messages || [];

      if (messages.length === 0) {
        addSystemMessage('会话无历史消息');
      } else {
        for (var i = 0; i < messages.length; i++) {
          var msg = messages[i];
          var role = msg.role === 'user' ? 'user' : 'agent';
          addMessage(role, msg.content);
        }
      }

      hasMoreMessages = data.has_more || false;
      oldestMessageId = data.oldest_id || null;

      if (hasMoreMessages) {
        addLoadMoreButton(userId, sessionId);
      }
    })
    .catch(function (err) {
      addMessage('error', '加载会话失败: ' + err.message);
    });

  updateSessionListHighlight();
  inputEl.focus();
}

function addLoadMoreButton(userId, sessionId) {
  var existing = messagesEl.querySelector('.load-more-btn');
  if (existing) existing.remove();

  var btn = document.createElement('div');
  btn.className = 'load-more-btn';
  btn.innerHTML = '<button onclick="loadMoreMessages(\'' + userId + '\',\'' + sessionId + '\',this)">加载更多历史消息</button>';
  messagesEl.insertBefore(btn, messagesEl.firstChild);
}

function loadMoreMessages(userId, sessionId, btn) {
  if (!hasMoreMessages || !oldestMessageId) return;
  btn.textContent = '加载中...';
  btn.disabled = true;

  fetch('/sessions/' + encodeURIComponent(userId) + '/' + encodeURIComponent(sessionId) + '/messages?before_id=' + oldestMessageId + '&limit=50')
    .then(function (resp) { return resp.json(); })
    .then(function (data) {
      var messages = data.messages || [];
      if (messages.length > 0) {
        var fragment = document.createDocumentFragment();
        for (var i = 0; i < messages.length; i++) {
          var msg = messages[i];
          var role = msg.role === 'user' ? 'user' : 'agent';
          var div = document.createElement('div');
          div.className = 'msg ' + role;
          if (role === 'agent') {
            div.innerHTML = '<div class="markdown-body">' + renderMarkdown(msg.content) + '</div>';
            highlightCodeBlocks(div);
          } else {
            div.textContent = msg.content;
          }
          fragment.appendChild(div);
        }
        btn.parentElement.remove();
        messagesEl.insertBefore(fragment, messagesEl.firstChild);

        hasMoreMessages = data.has_more || false;
        oldestMessageId = data.oldest_id || null;

        if (hasMoreMessages) addLoadMoreButton(userId, sessionId);
      }
    })
    .catch(function () {
      btn.textContent = '加载失败，点击重试';
      btn.disabled = false;
    });
}

function updateSessionListHighlight() {
  var items = sessionListEl.querySelectorAll('.session-item');
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var onclick = item.getAttribute('onclick') || '';
    if (onclick.indexOf("'" + currentSessionId + "'") !== -1 && onclick.indexOf('switchSession') === 0) {
      item.classList.add('active');
    } else {
      item.classList.remove('active');
    }
  }
}

// ============ View Toggle ============
function showMessagesView() {
  heroAreaEl.style.display = 'none';
  messagesAreaEl.style.display = 'flex';
  document.getElementById('composerWrap').className = 'composer-wrap';
  inputEl.placeholder = '输入消息... (Enter 发送)';
}

function showHeroView() {
  heroAreaEl.style.display = 'flex';
  messagesAreaEl.style.display = 'none';
  document.getElementById('composerWrap').className = 'composer-wrap hero-composer-wrap';
  inputEl.placeholder = '有什么可以帮你的？';
}

// ============ User Switch Dialog ============
function showUserDialog() {
  document.getElementById('currentUserLabel').textContent = currentUserId;
  document.getElementById('newUserInput').value = currentUserId;
  document.getElementById('userModal').style.display = 'flex';
  setTimeout(function () { document.getElementById('newUserInput').focus(); }, 50);
}

function closeUserDialog() {
  document.getElementById('userModal').style.display = 'none';
}

function doSwitchUser() {
  var newId = document.getElementById('newUserInput').value.trim();
  if (!newId || newId === currentUserId) {
    closeUserDialog();
    return;
  }
  currentUserId = newId;
  localStorage.setItem('user_id', newId);
  userDisplayEl.textContent = newId;
  currentSessionId = '';
  messagesEl.innerHTML = '';
  showHeroView();
  closeUserDialog();
  loadSessionList();
}

// ============ Rename Dialog ============
var renameTargetSessionId = '';

function showRenameDialog(sessionId, currentTitle) {
  renameTargetSessionId = sessionId;
  document.getElementById('renameInput').value = currentTitle;
  document.getElementById('renameModal').style.display = 'flex';
  setTimeout(function () {
    var inp = document.getElementById('renameInput');
    inp.focus();
    inp.select();
  }, 50);
}

function closeRenameDialog() {
  document.getElementById('renameModal').style.display = 'none';
  renameTargetSessionId = '';
}

function doRename() {
  var newTitle = document.getElementById('renameInput').value.trim();
  if (!newTitle || !renameTargetSessionId) {
    closeRenameDialog();
    return;
  }

  fetch('/sessions/' + encodeURIComponent(currentUserId) + '/' + encodeURIComponent(renameTargetSessionId) + '/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: newTitle }),
  })
    .then(function () {
      closeRenameDialog();
      loadSessionList();
    })
    .catch(function (err) {
      closeRenameDialog();
      addMessage('error', '重命名失败: ' + err.message);
    });
}

// ============ Delete Dialog ============
var deleteTargetSessionId = '';

function showDeleteDialog(sessionId, title) {
  deleteTargetSessionId = sessionId;
  document.getElementById('deleteTitle').textContent = title || sessionId.slice(0, 8);
  document.getElementById('deleteModal').style.display = 'flex';
}

function closeDeleteDialog() {
  document.getElementById('deleteModal').style.display = 'none';
  deleteTargetSessionId = '';
}

function doDelete() {
  if (!deleteTargetSessionId) { closeDeleteDialog(); return; }

  fetch('/sessions/' + encodeURIComponent(currentUserId) + '/' + encodeURIComponent(deleteTargetSessionId) + '/delete', {
    method: 'POST',
  })
    .then(function () {
      closeDeleteDialog();
      if (deleteTargetSessionId === currentSessionId) {
        currentSessionId = '';
        messagesEl.innerHTML = '';
        showHeroView();
      }
      loadSessionList();
    })
    .catch(function (err) {
      closeDeleteDialog();
      addMessage('error', '删除失败: ' + err.message);
    });
}

// ============ New Session ============
function newSession() {
  currentSessionId = '';
  messagesEl.innerHTML = '';
  showHeroView();
  updateSessionListHighlight();
  inputEl.focus();
}

// ============ Messages ============
function addMessage(role, text, extra) {
  // Switch to messages view if in hero
  if (heroAreaEl.style.display !== 'none') {
    showMessagesView();
  }

  var div = document.createElement('div');
  div.className = 'msg ' + role;
  div.style.animation = 'fadeIn 0.2s ease';

  if (role === 'tool') {
    div.innerHTML = '<div class="msg-label">🔧 ' + escapeHtml(extra || '工具调用') + '</div><pre class="tool-content">' + escapeHtml(text) + '</pre>';
  } else if (role === 'tool-result') {
    div.innerHTML = '<div class="msg-label">📋 工具结果</div><pre class="tool-content">' + escapeHtml(text) + '</pre>';
  } else if (role === 'error') {
    div.textContent = text;
  } else if (role === 'agent') {
    div.innerHTML = '<div class="markdown-body">' + renderMarkdown(text) + '</div>';
    highlightCodeBlocks(div);
  } else {
    div.textContent = text;
  }

  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function addSystemMessage(text) {
  var div = document.createElement('div');
  div.className = 'msg-system';
  div.textContent = text;
  messagesEl.appendChild(div);
  scrollToBottom();
}

function scrollToBottom() {
  var area = messagesAreaEl;
  area.scrollTop = area.scrollHeight;
}

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ============ SSE Event Handling ============
function handleSSEEvent(eventType, data, state) {
  switch (eventType) {
    case 'session':
      if (data.session_id && !currentSessionId) {
        currentSessionId = data.session_id;
      }
      break;

    case 'text_delta':
      if (data.delta) {
        state.agentText += data.delta;
        scheduleRender(state);
      }
      break;

    case 'thinking_delta':
      if (data.delta) {
        if (!state.thinkingBlock) {
          state.thinkingBlock = createThinkingBlock();
        }
        state.thinkingContent += data.delta;
        updateThinkingContent(state.thinkingBlock, state.thinkingContent);
      }
      break;

    case 'tool_call':
      if (data.tool_name) {
        var toolInfo = data.tool_args ? data.tool_name + '\n' + JSON.stringify(data.tool_args, null, 2) : data.tool_name;
        addMessage('tool', toolInfo, data.tool_name);
      }
      break;

    case 'tool_result':
      if (data.result) {
        addMessage('tool-result', typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2));
      }
      break;

    case 'reply_end':
      // Final render
      renderAgentMessage(state);
      break;

    case 'error':
      if (data.message) {
        addMessage('error', '错误: ' + data.message);
      }
      break;
  }
  scrollToBottom();
}

/** rAF 节流渲染 — 避免每个 delta 都重建 DOM */
function scheduleRender(state) {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(function () {
    renderPending = false;
    renderAgentMessage(state);
  });
}

function renderAgentMessage(state) {
  var agentDiv = state.agentDiv;
  agentDiv.innerHTML = '';

  if (state.thinkingBlock) {
    agentDiv.appendChild(state.thinkingBlock);
  }

  var contentDiv = document.createElement('div');
  contentDiv.className = 'markdown-body';
  contentDiv.innerHTML = renderMarkdown(state.agentText);
  agentDiv.appendChild(contentDiv);

  // Apply syntax highlighting
  highlightCodeBlocks(contentDiv);
}

function createThinkingBlock() {
  var details = document.createElement('details');
  details.className = 'thinking-block';
  details.open = true;

  var summary = document.createElement('summary');
  summary.className = 'thinking-header';
  summary.innerHTML = '<span class="thinking-icon">💭</span> <span class="thinking-title">思考过程</span>';

  var content = document.createElement('div');
  content.className = 'thinking-content';

  details.appendChild(summary);
  details.appendChild(content);

  // Auto-collapse after 3s
  setTimeout(function () { details.open = false; }, 3000);
  return details;
}

function updateThinkingContent(thinkingBlock, content) {
  var contentDiv = thinkingBlock.querySelector('.thinking-content');
  if (contentDiv) contentDiv.textContent = content;
}

// ============ Send Message ============
function send() {
  var text = inputEl.value.trim();
  if (!text || isStreaming) return;

  addMessage('user', text);
  inputEl.value = '';
  inputEl.style.height = 'auto';
  sendBtn.disabled = true;

  isStreaming = true;
  sendBtn.style.display = 'none';
  stopBtn.style.display = 'flex';

  var abortController = new AbortController();
  currentAbortController = abortController;
  var timeoutId = setTimeout(function () { abortController.abort(); }, 300000);

  // Add streaming dots
  var dotsDiv = document.createElement('div');
  dotsDiv.className = 'streaming-dots';
  dotsDiv.innerHTML = '<span></span><span></span><span></span>';
  messagesEl.appendChild(dotsDiv);
  scrollToBottom();

  var agentDiv = addMessage('agent', '');
  var state = {
    agentDiv: agentDiv,
    agentText: '',
    thinkingBlock: null,
    thinkingContent: '',
  };

  fetch('/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: text,
      user_id: currentUserId,
      session_id: currentSessionId || undefined,
    }),
    signal: abortController.signal,
  })
    .then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status + ': ' + resp.statusText);

      // Remove streaming dots
      if (dotsDiv.parentNode) dotsDiv.remove();

      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      var currentEventType = '';

      function read() {
        return reader.read().then(function (result) {
          if (result.done) return;

          buffer += decoder.decode(result.value, { stream: true });
          var lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line.indexOf('event: ') === 0) {
              currentEventType = line.slice(7).trim();
              continue;
            }
            if (line.indexOf('data: ') === 0) {
              try {
                var data = JSON.parse(line.slice(6));
                handleSSEEvent(currentEventType, data, state);
                currentEventType = '';
              } catch (e) { /* ignore parse errors */ }
            }
          }

          return read();
        });
      }

      return read();
    })
    .catch(function (err) {
      // Remove streaming dots on error
      if (dotsDiv.parentNode) dotsDiv.remove();

      if (err.name === 'AbortError') {
        addSystemMessage('⏹️ 已停止生成');
      } else {
        addMessage('error', '请求失败: ' + err.message);
      }
    })
    .then(function () {
      clearTimeout(timeoutId);
      currentAbortController = null;
      isStreaming = false;
      sendBtn.style.display = 'flex';
      stopBtn.style.display = 'none';
      sendBtn.disabled = !inputEl.value.trim();

      loadSessionList();
      inputEl.focus();
    });
}

function stop() {
  if (currentAbortController) currentAbortController.abort();
}

// ============ Composer Events ============
function setupComposerEvents() {
  inputEl.addEventListener('input', function () {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 150) + 'px';
    sendBtn.disabled = !inputEl.value.trim();
  });

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
}

// ============ Keyboard shortcuts for modals ============
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    closeUserDialog();
    closeRenameDialog();
    closeDeleteDialog();
  }
});

// ============ Init on DOM ready ============
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}