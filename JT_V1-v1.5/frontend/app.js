const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const root = document.documentElement;
const appShell = $('.app-shell');
const homeShell = $('[data-home]');
const caseWorkspace = $('[data-case-workspace]');
const workbench = $('.workbench');
const menu = $('[data-menu]');
const caseMenu = $('[data-case-menu]');
const toast = $('[data-toast]');
const settingsDialog = $('[data-settings-dialog]');
const sidebarResizer = $('[data-resizer="sidebar"]');
const railResizer = $('[data-resizer="rail"]');
const APPEARANCE_KEY = 'jt-appearance-v1';
const API_PRESET_KEY = 'jt-api-presets-v1';
const appearanceDefaults = {
  theme:'dark', accent:'cyan', background:'default', foreground:'default',
  uiFont:'system', uiWeight:'regular', contentFont:'same', contentWeight:'regular', codeFont:'consolas',
  sidebarGlass:true, contrast:60, pointer:true, motion:'system', uiScale:100, codeSize:12, diff:'color'
};
const loadAppearance = () => {
  try { return {...appearanceDefaults, ...JSON.parse(localStorage.getItem(APPEARANCE_KEY) || '{}')}; }
  catch { return {...appearanceDefaults}; }
};
const state = {
  cases: [], currentCase: null, currentConversation: null, contextCase: null, contextConversation: null,
  turns: [], analysis: null, groups: [], groupMessages: [], activeGroup: null,
  graph: null, graphCaseId: null, graphLoading: null, activeGraphSubject: null,
  expandedGraphNodes: new Set(), expandedGraphProfiles: new Set(), selectedGraphNode: null, collapsedGraphGroups: new Set(),
  graphLayouts: new Map(), graphViewport: {scale:1, x:0, y:0}, graphPointer: null,
  graphSubjectDeleteMode: false, graphAddingSubject: false, pendingGraphLink: null, addingRelatedTo: null, pendingNodePlace: null,
  graphUndo: [], graphFocusNode: null, graphMenuNode: null,
  graphEdgeFilters: {related: true, inferred: true, friend: true},
  friendPicks: new Set(), friendScreen: null,
  evidenceKind: 'support', highlightedSource: null, taskId: null, settings: null, usageTimer: null,
  appearance: loadAppearance(), caseQuery: '', caseFilter: 'all',
  view: 'home', settingsScope: 'case', pendingConversationCase: null, pendingQuestion: '', pendingStatus: '',
  importScope: 'case', importOwnerName: '', hasCaseMaterials: false, conversationSourceCount: 0,
  noteTimer: null, reviewDecision: '',
  modelLists: {local: [], api: []},
  chatLabels: {groups: {}, senders: {}}, persons: [], showHiddenGroups: false
};
let toastTimer;

function applyAppearance(persist = false) {
  const value = state.appearance;
  const resolvedTheme = value.theme === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : value.theme;
  root.dataset.theme = resolvedTheme;
  ['accent','background','foreground','uiFont','uiWeight','contentFont','contentWeight','codeFont','motion','diff'].forEach(key => { root.dataset[key] = value[key]; });
  root.dataset.sidebarGlass = String(value.sidebarGlass);
  root.dataset.pointer = String(value.pointer);
  root.style.setProperty('--ui-scale', `${value.uiScale}%`);
  root.style.setProperty('--ui-contrast', String(0.7 + Number(value.contrast) / 200));
  root.style.setProperty('--code-text-size', `${Number(value.codeSize) / 16}rem`);
  if (persist) localStorage.setItem(APPEARANCE_KEY, JSON.stringify(value));
}

function syncAppearanceControls() {
  $$('[data-appearance]').forEach(input => {
    const value = state.appearance[input.dataset.appearance];
    if (input.type === 'checkbox') input.checked = Boolean(value);
    else input.value = value;
  });
  $('[data-appearance-output="contrast"]').textContent = state.appearance.contrast;
  $('[data-appearance-output="uiScale"]').textContent = `${state.appearance.uiScale}%`;
  $('[data-appearance-output="codeSize"]').textContent = state.appearance.codeSize;
}

applyAppearance();

const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

function highlightInferenceStatement(text) {
  const html = escapeHtml(text);
  const spans = [];
  const add = (start, end, cls) => {
    if (end <= start) return;
    if (spans.some(item => start < item.end && end > item.start)) return;
    spans.push({start, end, cls});
  };
  const group = html.match(/^群(?:「|&quot;|“|")[^」&quot;”"]{1,80}(?:」|&quot;|”|")|^群[A-Za-z0-9_-]+|^群[^\s，,。；;：:（()疑似]{1,40}(?=疑似)/);
  if (group) add(0, group[0].length, 'infer-group');
  for (const re of [
    /(疑似为|疑似是)(.+?)(?=：|:|；|;|，|,|成员包括|成员仅|聊天内容涉及|内容涉及|$)/g,
    /(成员包括|成员仅)(.+?)(?=，聊天内容涉及|，内容涉及|。|；|;|聊天内容涉及|内容涉及|$)/g,
  ]) {
    let match;
    while ((match = re.exec(html))) {
      add(match.index, match.index + match[1].length, 'infer-plain');
      add(match.index + match[1].length, match.index + match[0].length, 'infer-value');
    }
  }
  const topicRe = /((?:聊天)?内容涉及)([^。]*)/g;
  let topic;
  while ((topic = topicRe.exec(html))) {
    add(topic.index, topic.index + topic[1].length, 'infer-topic-label');
    add(topic.index + topic[1].length, topic.index + topic[0].length, 'infer-topic');
  }
  spans.sort((a, b) => a.start - b.start);
  let out = '';
  let cursor = 0;
  for (const span of spans) {
    out += html.slice(cursor, span.start);
    out += `<span class="${span.cls}">${html.slice(span.start, span.end)}</span>`;
    cursor = span.end;
  }
  return out + html.slice(cursor);
}
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '时间未知';
const shortId = value => value ? String(value).slice(0, 10).toUpperCase() : '--';
const subjectsFromQuestion = question => {
  const subjectText = String(question || '')
    .replace(/[，。？！?]/g, '')
    .replace(/^(?:我想|我问|请问|查询|查看|研判|分析|总结)+/, '')
    .split(/的?(?:关系|信息|情况)/)[0];
  return subjectText.split(/和|与|、/).map(part => part.match(/[\u4e00-\u9fff]{2,4}$/)?.[0]).filter(Boolean);
};
const showToast = message => {
  toast.textContent = message;
  toast.dataset.visible = 'true';
  clearTimeout(toastTimer);
  const wait = String(message || '').length > 80 ? 8000 : 2600;
  toastTimer = setTimeout(() => { toast.dataset.visible = 'false'; }, wait);
};
const api = async (path, options = {}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs || 20000);
  let response;
  try {
    response = await fetch(path, {...options, signal: controller.signal});
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请刷新页面');
    throw error;
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 204) return null;
  const type = response.headers.get('content-type') || '';
  const payload = type.includes('json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map(item => item.msg || JSON.stringify(item)).join('；')
        : (payload || `请求失败：${response.status}`);
    throw new Error(message);
  }
  return payload;
};
const jsonOptions = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

function setBackendStatus(label, connected = true) {
  $$('[data-backend-status]').forEach(chip => {
    const text = chip.querySelector('.zh-label');
    if (text) text.textContent = label;
    chip.dataset.state = connected ? 'connected' : 'error';
  });
}

function showHome() {
  state.view = 'home';
  state.currentCase = null;
  state.currentConversation = null;
  state.analysis = null;
  state.highlightedSource = null;
  state.graph = null;
  state.graphCaseId = null;
  homeShell.hidden = false;
  caseWorkspace.hidden = true;
  history.replaceState(null, '', location.pathname + location.search);
  renderHomeCases();
}

function showCaseWorkspace() {
  state.view = 'case';
  homeShell.hidden = true;
  caseWorkspace.hidden = false;
}

function filteredHomeCases() {
  const query = state.caseQuery.trim().toLowerCase();
  return state.cases.filter(caseItem => {
    if (state.caseFilter === 'pinned' && !caseItem.pinned) return false;
    if (state.caseFilter === 'active' && caseItem.archived) return false;
    if (state.caseFilter === 'archived' && !caseItem.archived) return false;
    if (!query) return true;
    return [caseItem.name, caseItem.code, caseItem.section, caseItem.category, caseItem.handler, caseItem.unit].some(value => String(value || '').toLowerCase().includes(query));
  }).sort((left, right) => Number(right.pinned) - Number(left.pinned) || String(right.created_at || '').localeCompare(String(left.created_at || '')));
}

function renderHomeCases() {
  const list = $('[data-home-cases]');
  const visible = filteredHomeCases();
  const conversations = state.cases.reduce((total, item) => total + (item.conversations || []).length, 0);
  const count = $('[data-home-count]');
  const conversationCount = $('[data-home-conversation-count]');
  if (count) count.textContent = String(state.cases.length);
  if (conversationCount) conversationCount.textContent = String(conversations);
  if (!state.cases.length) {
    list.innerHTML = '<p class="empty-state">还没有案件。点击「新建案件」填写案件基本信息后开始。</p>';
    return;
  }
  if (!visible.length) {
    list.innerHTML = '<p class="empty-state">没有匹配的案件。可改检索词，或切换筛选条件。</p>';
    return;
  }
  list.innerHTML = visible.map(caseItem => {
    const items = caseItem.conversations || [];
    const created = caseItem.created_at ? formatTime(caseItem.created_at) : '';
    return `<article class="case-card" data-pinned="${caseItem.pinned}" data-archived="${caseItem.archived}" data-section="${escapeHtml(caseItem.section || '')}">
      <button class="case-card-main" type="button" data-home-case="${escapeHtml(caseItem.id)}">
        <div class="case-card-head">
          <svg class="icon compact pin-indicator"><use href="#i-pin"/></svg>
          <svg class="icon"><use href="#i-case"/></svg>
          <strong>${escapeHtml(caseItem.name)}</strong>
        </div>
        <div class="case-card-meta"><span>${escapeHtml(caseItem.code)}</span>${caseItem.category ? `<span>${escapeHtml(caseItem.category)}</span>` : ''}${created ? `<span>${escapeHtml(created)}</span>` : ''}</div>
        <div class="case-card-foot">
          <span>${items.length} 条对话</span>
          <span>${escapeHtml(caseItem.section || '默认')}</span>
          ${caseItem.archived ? '<span>已归档</span>' : ''}
        </div>
      </button>
      <div class="case-card-actions">
        <button class="button" type="button" data-pin-case="${escapeHtml(caseItem.id)}" aria-pressed="${caseItem.pinned ? 'true' : 'false'}">
          <svg class="icon compact"><use href="#i-pin"/></svg>${caseItem.pinned ? '取消置顶' : '置顶'}
        </button>
        <button class="button" type="button" data-archive-case="${escapeHtml(caseItem.id)}" aria-pressed="${caseItem.archived ? 'true' : 'false'}">
          <svg class="icon compact"><use href="#i-archive"/></svg>${caseItem.archived ? '恢复' : '归档'}
        </button>
        <button class="button danger case-card-delete" type="button" data-delete-case="${escapeHtml(caseItem.id)}" aria-label="删除案件 ${escapeHtml(caseItem.name)}">
          <svg class="icon compact"><use href="#i-reject"/></svg>删除案件
        </button>
      </div>
    </article>`;
  }).join('');
}

function renderCases() {
  const list = $('[data-case-list]');
  const caseItem = state.currentCase;
  if (!caseItem) {
    list.innerHTML = '<p class="empty-state">返回项目后选择案件</p>';
    return;
  }
  const conversations = caseItem.conversations || [];
  list.innerHTML = `<section class="tree-group" data-case-section="${escapeHtml(caseItem.id)}">
      <div class="conversation-list">${conversations.length ? conversations.map(conversation => `
        <div class="conversation-row">
          <button class="conversation-item" ${state.currentConversation?.id === conversation.id ? 'aria-current="page"' : ''}
            data-conversation-id="${escapeHtml(conversation.id)}" data-title="${escapeHtml(conversation.title)}"
            data-pinned="${conversation.pinned}" data-unread="${conversation.unread}" data-archived="${conversation.archived}">
            <svg class="icon compact pin-indicator"><use href="#i-pin"/></svg><svg class="icon compact"><use href="#i-chat"/></svg>
            <span class="truncate">${escapeHtml(conversation.title)}</span>
          </button>
          <button class="button icon-only conversation-delete" type="button" data-delete-conversation="${escapeHtml(conversation.id)}" aria-label="删除研判对话 ${escapeHtml(conversation.title)}">
            <svg class="icon compact"><use href="#i-reject"/></svg>
          </button>
        </div>`).join('') : '<span class="empty-tree">暂无研判对话</span>'}</div>
    </section>`;
}

async function loadCases(caseId = null, conversationId = null) {
  state.cases = await api('/cases');
  await Promise.all(state.cases.map(async item => { item.conversations = await api(`/cases/${item.id}/conversations`); }));
  setBackendStatus('后端已连接', true);
  renderHomeCases();
  renderCases();
  const params = new URLSearchParams(location.hash.slice(1));
  const hashConversation = conversationId || params.get('conversation');
  const hashCase = caseId || params.get('case');
  const selected = state.cases.flatMap(item => (item.conversations || []).map(conversation => ({item, conversation}))).find(row => row.conversation.id === hashConversation);
  const selectedCase = selected?.item || state.cases.find(item => item.id === hashCase);
  if (selectedCase) await selectCase(selectedCase, selected?.conversation || null);
  else {
    if (hashCase) showToast('当前链接里的案件已经不存在，已回到案件列表');
    showHome();
  }
}

function resetWorkspace() {
  state.currentCase = state.currentConversation = state.analysis = null;
  state.highlightedSource = null;
  state.graph = null;
  state.graphCaseId = null;
  state.graphUndo = [];
  state.graphFocusNode = null;
  state.graphMenuNode = null;
  state.graphEdgeFilters = {related: true, inferred: true, friend: true};
  state.chatLabels = {groups: {}, senders: {}};
  state.persons = [];
  state.showHiddenGroups = false;
  $('[data-case-name]').textContent = '暂无案件';
  $('[data-case-meta]').textContent = '请先新建案件';
  $('[data-turns]').innerHTML = '<p class="empty-state">新建案件与研判对话后开始工作</p>';
  loadCaseNote();
  renderEvidence();
  renderGroups();
  renderGraph();
  showHome();
}

async function selectCase(caseItem, conversation = null) {
  if (!conversation && (caseItem.conversations || []).length === 1) conversation = caseItem.conversations[0];
  showCaseWorkspace();
  state.currentCase = caseItem;
  state.currentConversation = null;
  state.turns = [];
  state.analysis = null;
  state.highlightedSource = null;
  state.graph = null;
  state.graphCaseId = null;
  state.activeGraphSubject = null;
  state.graphUndo = [];
  state.graphFocusNode = null;
  state.graphMenuNode = null;
  state.graphEdgeFilters = {related: true, inferred: true, friend: true};
  state.expandedGraphNodes.clear();
  state.expandedGraphProfiles.clear();
  state.selectedGraphNode = null;
  state.collapsedGraphGroups.clear();
  state.friendPicks.clear();
  state.friendScreen = null;
  $('[data-case-name]').textContent = caseItem.name;
  $('[data-case-meta]').textContent = `${caseItem.code} / ${conversation?.title || '请选择研判对话'}`;
  if (!conversation) history.replaceState(null, '', `#case=${caseItem.id}`);
  renderCases();
  renderHomeCases();
  loadCaseNote();
  await loadChatWorkspace();
  await Promise.all([loadGroups(), loadRuntimeSettings(), refreshSetupStatus(), workbench.dataset.workspaceMode === 'graph' ? loadGraph() : null]);
  if (conversation) await selectConversation(conversation);
  else {
    $('[data-turns]').innerHTML = '<p class="empty-state">请先导入案件材料并新建研判对话。未点材料智能分析前，只能交流案件基本信息和文件清单</p>';
    renderEvidence();
  }
}

const materialQuery = extra => {
  const params = new URLSearchParams(extra || {});
  if (state.currentConversation?.id) params.set('conversation_id', state.currentConversation.id);
  const query = params.toString();
  return query ? `?${query}` : '';
};

function setWorkbenchStage(stage) {
  workbench.dataset.stage = stage;
  workbench.dataset.chatReady = String(Boolean(state.currentConversation));
  workbench.dataset.materials = String(Boolean(state.hasCaseMaterials));
}

async function refreshSetupStatus() {
  const sourceStatus = $('[data-source-status]');
  const conversationStatus = $('[data-conversation-status]');
  if (!sourceStatus || !conversationStatus) return;
  const question = $('[data-question]');
  const ready = Boolean(state.currentConversation?.analysis_enabled);
  if (!state.currentCase) {
    state.hasCaseMaterials = false;
    sourceStatus.textContent = '尚未导入案件材料';
    conversationStatus.textContent = '尚未创建对话';
    setWorkbenchStage('import');
    if (question) question.placeholder = '输入问题，可继续追问人员、账号、地址或事件';
    return;
  }
  try {
    const stats = await api(`/cases/${state.currentCase.id}/stats`);
    state.hasCaseMaterials = (stats.case_source_files || 0) > 0;
    sourceStatus.textContent = state.hasCaseMaterials
      ? `已导入 ${stats.case_source_files} 个案件文件，${stats.messages} 条消息`
      : '尚未导入案件材料';
  } catch {
    state.hasCaseMaterials = false;
    sourceStatus.textContent = '尚未导入案件材料';
  }
  try {
    const sources = await api(`/cases/${state.currentCase.id}/sources`);
    const conversationId = state.currentConversation?.id;
    state.conversationSourceCount = conversationId
      ? sources.filter(item => item.conversation_id === conversationId).length
      : 0;
  } catch {
    state.conversationSourceCount = 0;
  }
  const count = state.currentCase.conversations?.length || 0;
  conversationStatus.textContent = state.currentConversation
    ? (ready
      ? `当前对话：${state.currentConversation.title}，已启动材料智能分析`
      : `当前对话：${state.currentConversation.title}。群聊和图谱可直接查看，重点标记需点材料智能分析`)
    : (count ? `已有 ${count} 条研判对话，请选择或新建` : '尚未创建对话');
  if (question) {
    question.placeholder = ready
      ? '输入问题，可继续追问人员、账号、地址或事件'
      : '可询问案件名称、承办人、已上传文件；材料内容需先点材料智能分析';
  }
  setWorkbenchStage(ready ? 'analysis' : (state.currentConversation ? 'conversation' : 'import'));
}

const fillModelSelect = (mode, models, selected) => {
  const select = $(`[data-${mode}-model]`);
  if (!select) return;
  const names = [...new Set((models || []).filter(Boolean))];
  if (selected && !names.includes(selected)) names.unshift(selected);
  select.innerHTML = names.length
    ? names.map(model => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join('')
    : '<option value="">请先获取模型</option>';
  select.value = (selected && names.includes(selected)) ? selected : (names[0] || '');
};

const selectedApiModel = () => {
  const listed = $('[data-api-model]')?.value.trim() || '';
  const custom = $('[data-api-model-custom]')?.value.trim() || '';
  const fetched = state.modelLists.api || [];
  if (fetched.length) return (listed && fetched.includes(listed) ? listed : '') || (custom && fetched.includes(custom) ? custom : '') || listed || custom || fetched[0];
  return listed || custom;
};

async function loadRuntimeSettings() {
  if (!state.currentCase) return;
  state.settings = await api(`/cases/${state.currentCase.id}/settings`);
  const model = state.settings.model;
  $('[data-active-model]').textContent = model.mode === 'api' ? (model.api.model || 'API 模型') : (model.local.model || '本地模型');
}

function updateParameterLabels() {
  $('[data-temperature-value]').textContent = Number($('[data-temperature]').value).toFixed(2);
  $('[data-top-p-value]').textContent = Number($('[data-top-p]').value).toFixed(2);
}

function renderPrompts(prompts = state.settings.system_prompts) {
  $('[data-prompt-list]').innerHTML = prompts.map(item => `<article class="prompt-item" data-prompt-id="${escapeHtml(item.id)}" data-builtin="${Boolean(item.builtin)}"><div class="prompt-item-head"><input aria-label="提示词名称" value="${escapeHtml(item.name)}"><label class="switch"><input type="checkbox" data-prompt-enabled ${item.enabled ? 'checked' : ''}><span></span></label></div><textarea aria-label="${escapeHtml(item.name)}内容" placeholder="输入提示词内容" ${item.builtin ? 'readonly' : ''}>${escapeHtml(item.content)}</textarea></article>`).join('');
}

function updateSkillSelectAll() {
  const boxes = $$('[data-skill-list] input[type="checkbox"]');
  const control = $('[data-skill-select-all]');
  control.checked = boxes.length > 0 && boxes.every(box => box.checked);
  control.indeterminate = boxes.some(box => box.checked) && !control.checked;
}

function addPrompt() {
  state.settings.system_prompts.push({id:`prompt-${Date.now()}`, name:'新提示词', enabled:true, content:'', builtin:false});
  renderPrompts();
  $('[data-prompt-list] .prompt-item:last-child input[aria-label="提示词名称"]').select();
}

async function refreshUsage() {
  if (!state.currentCase || !settingsDialog.open) return;
  const usage = await api(`/cases/${state.currentCase.id}/usage`);
  ['requests','prompt_tokens','completion_tokens','total_tokens'].forEach(key => { $(`[data-usage="${key}"]`).textContent = usage[key].toLocaleString('zh-CN'); });
  const events = usage.events.slice(-16);
  const max = Math.max(1, ...events.map(item => item.total_tokens));
  $('[data-usage-chart]').innerHTML = events.length ? events.map(item => `<span class="usage-bar" style="--usage-ratio:${item.total_tokens / max}" title="${escapeHtml(item.engine)} / ${item.total_tokens} Tokens"></span>`).join('') : '<p class="empty-state">暂无调用数据</p>';
}

function setSettingsScope(scope) {
  state.settingsScope = scope;
  const caseOnly = new Set(['skills', 'mcp', 'prompt', 'model', 'usage']);
  $$('[data-settings-tab]').forEach(tab => {
    tab.hidden = scope === 'home' && caseOnly.has(tab.dataset.settingsTab);
    if (scope === 'home' && tab.dataset.settingsTab === 'appearance') tab.setAttribute('aria-current', 'page');
    else if (scope === 'home') tab.removeAttribute('aria-current');
  });
  $$('[data-settings-panel]').forEach(panel => {
    panel.hidden = scope === 'home' ? panel.dataset.settingsPanel !== 'appearance' : panel.dataset.settingsPanel !== 'skills';
  });
  if (scope === 'case') {
    $$('[data-settings-tab]').forEach(tab => tab.toggleAttribute('aria-current', tab.dataset.settingsTab === 'skills'));
    $('[data-settings-status]').textContent = '配置保存在当前案件目录';
    $('[data-settings-form] button[value="save"]').textContent = '保存设置';
  } else {
    $('[data-settings-case]').textContent = '系统外观';
    $('[data-settings-status]').textContent = '外观保存在当前浏览器。模型与提示词请进入案件后设置。';
    $('[data-settings-form] button[value="save"]').textContent = '完成';
  }
}

async function openSettings(scope = 'case') {
  if (scope === 'home' || !state.currentCase) {
    setSettingsScope('home');
    syncAppearanceControls();
    settingsDialog.showModal();
    return;
  }
  setSettingsScope('case');
  const [runtime, skills] = await Promise.all([api(`/cases/${state.currentCase.id}/settings`), api(`/cases/${state.currentCase.id}/skills`)]);
  state.settings = runtime;
  $('[data-settings-case]').textContent = `${state.currentCase.name} / ${state.currentCase.code}`;
  renderPrompts(runtime.system_prompts);
  $(`[name="model-mode"][value="${runtime.model.mode}"]`).checked = true;
  $('[data-local-base]').value = runtime.model.local.base_url;
  $('[data-local-enabled]').checked = runtime.model.local.enabled;
  $('[data-api-base]').value = runtime.model.api.base_url;
  $('[data-api-key]').value = '';
  $('[data-api-key]').placeholder = runtime.model.api.api_key_set ? '已保存，留空则不修改' : 'API Key';
  $('[data-api-model-custom]').value = '';
  $('[data-api-preset-name]').value = '';
  $('[data-api-preset-note]').value = '';
  fillModelSelect('local', state.modelLists.local, runtime.model.local.model);
  fillModelSelect('api', state.modelLists.api, runtime.model.api.model);
  hydrateApiPresets(runtime.model.api_presets);
  renderApiPresets();
  $('[data-temperature]').value = runtime.model.temperature;
  $('[data-top-p]').value = runtime.model.top_p;
  $('[data-max-tokens]').value = runtime.model.max_tokens;
  updateParameterLabels();
  $('[data-skill-count]').textContent = `${skills.length} AVAILABLE`;
  $('[data-skill-list]').innerHTML = skills.length ? skills.map(skill => `<label class="skill-item"><input type="checkbox" value="${escapeHtml(skill.id)}" ${skill.enabled ? 'checked' : ''}><span><strong>${escapeHtml(skill.name)}</strong><small>${escapeHtml(skill.description)}</small><em>${escapeHtml(skill.scope)}</em></span></label>`).join('') : '<p class="empty-state">当前没有可用 SKILL</p>';
  updateSkillSelectAll();
  syncAppearanceControls();
  settingsDialog.showModal();
  await refreshUsage();
  clearInterval(state.usageTimer);
  state.usageTimer = setInterval(() => refreshUsage().catch(() => {}), 2000);
}

function normalizeApiBase(value) {
  let url = String(value || '').trim();
  if (!url) return '';
  if (url.startsWith('//')) url = `https:${url}`;
  if (!/^https?:\/\//i.test(url)) url = (/^(localhost|127\.0\.0\.1)[:/]?/i.test(url) ? 'http://' : 'https://') + url;
  return url.replace(/\/+$/, '');
}

function collectSettingsPayload() {
  const localBase = normalizeApiBase($('[data-local-base]').value.trim() || state.settings?.model?.local?.base_url || 'http://127.0.0.1:1234/v1');
  const localModel = $('[data-local-model]').value || state.settings?.model?.local?.model || 'local-model';
  const apiBase = normalizeApiBase($('[data-api-base]').value.trim());
  if ($('[data-api-base]') && apiBase && $('[data-api-base]').value.trim() !== apiBase) $('[data-api-base]').value = apiBase;
  return {
    skills: $$('[data-skill-list] input:checked').map(input => input.value), mcp: [],
    system_prompts: $$('[data-prompt-id]').map(item => ({id:item.dataset.promptId, name:item.querySelector('input[aria-label="提示词名称"]').value.trim(), enabled:item.querySelector('[data-prompt-enabled]').checked, content:item.querySelector('textarea').value.trim(), builtin:item.dataset.builtin === 'true'})),
    model: {
      mode:$('[name="model-mode"]:checked')?.value || 'local',
      local:{base_url:localBase, model:localModel, enabled:$('[data-local-enabled]').checked},
      api:{base_url:apiBase, model:selectedApiModel(), api_key:$('[data-api-key]').value.trim()},
      api_presets: loadApiPresets().map(item => ({id:item.id, name:item.name, note:item.note || '', base_url:normalizeApiBase(item.base_url || ''), model:item.model || '', api_key:item.api_key || ''})),
      temperature:Number($('[data-temperature]').value), top_p:Number($('[data-top-p]').value), max_tokens:Number($('[data-max-tokens]').value),
    },
  };
}

function setActiveModelLabel(model = state.settings?.model) {
  if (!model) return;
  $('[data-active-model]').textContent = model.mode === 'api' ? (model.api?.model || 'API 模型') : (model.local?.model || '本地模型');
}

function switchToApiMode() {
  const radio = $('[name="model-mode"][value="api"]');
  if (radio) radio.checked = true;
}

function resolveApiKey() {
  const typed = $('[data-api-key]')?.value.trim();
  if (typed) return typed;
  const base = $('[data-api-base]')?.value.trim();
  const name = $('[data-api-preset-name]')?.value.trim();
  const presets = loadApiPresets();
  return (
    presets.find(item => name && item.name === name && item.api_key)?.api_key
    || presets.find(item => base && item.base_url === base && item.api_key)?.api_key
    || ''
  );
}

async function persistSettings({close = true, toastMessage = '设置已保存'} = {}) {
  if (!state.currentCase) throw new Error('请先选择案件');
  const payload = collectSettingsPayload();
  if (payload.model.mode === 'api' && !payload.model.api.base_url) throw new Error('请填写完整 API 地址，需以 http:// 或 https:// 开头');
  state.settings = await api(`/cases/${state.currentCase.id}/settings`, jsonOptions('PUT', payload));
  setActiveModelLabel(payload.model);
  if (close) settingsDialog.close();
  if (toastMessage) showToast(toastMessage);
  return state.settings;
}

async function loadModels(mode) {
  const input = $(`[data-${mode}-base]`);
  const base = normalizeApiBase(input?.value.trim());
  if (!base) return showToast('请填写完整接口地址，需以 http:// 或 https:// 开头');
  if (input) input.value = base;
  if (!state.currentCase) return showToast('请先选择案件');
  const apiKey = mode === 'api' ? resolveApiKey() : '';
  const result = await api(`/cases/${state.currentCase.id}/models`, jsonOptions('POST', {base_url:base, api_key:apiKey}));
  const models = result.models || [];
  state.modelLists[mode] = models;
  const current = mode === 'api' ? selectedApiModel() : $(`[data-${mode}-model]`).value;
  const preferred = models.includes(current) ? current : models[0];
  fillModelSelect(mode, models, preferred);
  if (mode === 'api') {
    const custom = $('[data-api-model-custom]').value.trim();
    if (custom && !models.includes(custom)) $('[data-api-model-custom]').value = '';
  }
  if (!models.length) return showToast('接口没有返回模型，可在自定义模型名中手动填写');
  showToast(`已读取 ${models.length} 个模型`);
  return models;
}

async function saveSettings() {
  await persistSettings();
}

async function reloadConversations(selectId = null) {
  if (!state.currentCase) return;
  state.currentCase.conversations = await api(`/cases/${state.currentCase.id}/conversations`);
  renderCases();
  const conversation = state.currentCase.conversations.find(item => item.id === selectId);
  if (conversation) await selectConversation(conversation);
  await refreshSetupStatus();
}

const loadApiPresets = () => {
  try { return JSON.parse(localStorage.getItem(API_PRESET_KEY) || '[]'); }
  catch { return []; }
};

const writeApiPresets = presets => {
  localStorage.setItem(API_PRESET_KEY, JSON.stringify(presets));
};

function hydrateApiPresets(saved = []) {
  const current = loadApiPresets();
  if (current.length || !saved?.length) return;
  writeApiPresets(saved.map(item => ({
    id: item.id,
    name: item.name,
    note: item.note || '',
    base_url: item.base_url || '',
    model: item.model || '',
    api_key: '',
    api_key_set: Boolean(item.api_key_set),
  })));
}

function renderApiPresets() {
  const list = $('[data-api-preset-list]');
  if (!list) return;
  const presets = loadApiPresets();
  list.innerHTML = presets.length
    ? presets.map(item => `<article class="api-preset-item" data-api-preset-id="${escapeHtml(item.id)}"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.note || item.model || item.base_url || '未填写说明')}</small></div><div class="api-preset-actions"><button class="button" type="button" data-api-preset-apply="${escapeHtml(item.id)}">应用</button><button class="button" type="button" data-api-preset-delete="${escapeHtml(item.id)}">删除</button></div></article>`).join('')
    : '<p class="empty-state">尚未保存 API 配置。填写名称后可自定义保存当前接口信息。</p>';
}

function currentApiFields() {
  return {
    base_url: normalizeApiBase($('[data-api-base]').value.trim()),
    model: selectedApiModel(),
    api_key: $('[data-api-key]').value.trim(),
  };
}

function saveApiPreset() {
  const name = $('[data-api-preset-name]').value.trim();
  if (!name) return showToast('请填写配置名称');
  const fields = currentApiFields();
  if (!fields.base_url) return showToast('请填写完整 API 地址，需以 http:// 或 https:// 开头');
  $('[data-api-base]').value = fields.base_url;
  const presets = loadApiPresets();
  const existing = presets.find(item => item.name === name);
  const preset = {
    id: existing?.id || (crypto.randomUUID ? crypto.randomUUID() : `api-${Date.now()}`),
    name,
    note: $('[data-api-preset-note]').value.trim(),
    ...fields,
    api_key: fields.api_key || existing?.api_key || '',
    api_key_set: Boolean(fields.api_key || existing?.api_key || existing?.api_key_set),
  };
  if (existing) Object.assign(existing, preset);
  else presets.unshift(preset);
  writeApiPresets(presets);
  renderApiPresets();
  switchToApiMode();
  return persistSettings({close:false, toastMessage:''})
    .then(() => loadModels('api'))
    .then(() => persistSettings({close:false, toastMessage:`已保存并切换到 API「${name}」`}));
}

async function applyApiPreset(id) {
  const preset = loadApiPresets().find(item => item.id === id);
  if (!preset) return;
  $('[data-api-base]').value = normalizeApiBase(preset.base_url || '');
  $('[data-api-model-custom]').value = '';
  $('[data-api-preset-name]').value = preset.name || '';
  $('[data-api-preset-note]').value = preset.note || '';
  fillModelSelect('api', state.modelLists.api, preset.model || '');
  if (preset.api_key) {
    $('[data-api-key]').value = preset.api_key;
    $('[data-api-key]').placeholder = '已填入保存的密钥';
  } else {
    $('[data-api-key]').value = '';
    $('[data-api-key]').placeholder = preset.api_key_set ? '本案已保存该配置密钥，留空则不修改' : 'API Key';
  }
  switchToApiMode();
  await persistSettings({close:false, toastMessage:''});
  await loadModels('api');
  await persistSettings({close:false, toastMessage:`已应用并切换到 API「${preset.name}」`});
}

function deleteApiPreset(id) {
  const presets = loadApiPresets();
  const preset = presets.find(item => item.id === id);
  if (!preset || !confirm(`删除 API 配置「${preset.name}」？`)) return;
  writeApiPresets(presets.filter(item => item.id !== id));
  renderApiPresets();
  showToast('已删除该 API 配置');
}

async function selectConversation(conversation) {
  state.currentConversation = conversation;
  clearPendingTurn();
  history.replaceState(null, '', `#conversation=${conversation.id}`);
  $('[data-case-meta]').textContent = `${state.currentCase.code} / ${conversation.title}`;
  renderCases();
  state.turns = await api(`/conversations/${conversation.id}/turns`);
  renderTurns();
  await refreshSetupStatus();
  await Promise.all([loadGroups(), workbench.dataset.workspaceMode === 'graph' || workbench.dataset.stage === 'analysis' ? loadGraph(true) : null]);
  if (matchMedia('(max-width: 48rem)').matches) setSidebarOpen(false);
}

function rememberConversation(updated) {
  if (!updated) return;
  if (state.currentConversation?.id === updated.id) state.currentConversation = {...state.currentConversation, ...updated};
  const listed = state.currentCase?.conversations?.find(item => item.id === updated.id);
  if (listed) Object.assign(listed, updated);
}

function scrollTurnsToEnd() {
  const stream = $('[data-turns]');
  if (!stream) return;
  const reduce = root.dataset.motion === 'reduce';
  requestAnimationFrame(() => {
    const top = stream.scrollHeight;
    if (!reduce && typeof stream.scrollTo === 'function') stream.scrollTo({top, behavior: 'smooth'});
    else stream.scrollTop = top;
  });
}

function pendingTurnMarkup() {
  if (!state.pendingQuestion) return '';
  return `<article class="turn is-pending" data-pending-turn>
      <div class="question">${escapeHtml(state.pendingQuestion)}</div>
      <div class="answer"><header class="result-heading"><h3>正在研判</h3></header>
        <section class="judgement-box">
          <p class="pending-status"><span class="pending-dots" aria-hidden="true"><span></span><span></span><span></span></span><span data-pending-status-text>${escapeHtml(state.pendingStatus || '已收到问题，正在检索材料并生成结果')}</span></p>
        </section></div></article>`;
}

function showPendingTurn(question, status) {
  state.pendingQuestion = question;
  state.pendingStatus = status || '已收到问题，正在检索材料并生成结果';
  renderTurns();
  scrollTurnsToEnd();
}

function updatePendingStatus(status) {
  state.pendingStatus = status;
  const text = $('[data-pending-status-text]');
  if (text) text.textContent = status;
}

function clearPendingTurn() {
  state.pendingQuestion = '';
  state.pendingStatus = '';
}

function renderTurns() {
  const stream = $('[data-turns]');
  if (!state.turns.length && !state.pendingQuestion) {
    state.analysis = null;
    stream.innerHTML = state.currentConversation?.analysis_enabled
      ? '<p class="empty-state">当前对话还没有研判记录</p>'
      : '<p class="empty-state">可询问案件名称、承办人、已上传文件。点击「材料智能分析」后，才会按材料内容研判</p>';
    renderEvidence();
    setReviewEnabled(false);
    return;
  }
  stream.innerHTML = state.turns.map(turn => {
    const result = turn.result || {};
    const claims = result.claims || [];
    const facts = claims.filter(claim => claim.type === 'fact');
    const inferences = claims.filter(claim => claim.type !== 'fact');
    const claimRows = (items, highlight = false) => items.map((claim, index) => `<div class="fact-row"><span class="fact-index">${String(index + 1).padStart(2, '0')}</span><p>${highlight ? highlightInferenceStatement(claim.statement) : escapeHtml(claim.statement)}</p>${claim.evidence_refs?.length ? `<div class="source-links">${claim.evidence_refs.map(ref => `<button class="source-link" data-source="${escapeHtml(ref)}" aria-label="查看证据 ${escapeHtml(shortId(ref))}">证据 ${escapeHtml(shortId(ref))}</button>`).join('')}</div>` : ''}</div>`).join('');
    const people = [...new Set((result.subjects?.length ? result.subjects : subjectsFromQuestion(turn.question)).filter(Boolean))];
    const sharedFacts = facts.filter(claim => !people.some(person => claim.statement.includes(person)));
    const personGroups = people.map(person => {
      const directFacts = facts.filter(claim => claim.statement.includes(person));
      const items = directFacts.length ? [...directFacts, ...sharedFacts] : (sharedFacts.length ? sharedFacts : facts);
      return {person, items:[...new Map(items.map(item => [JSON.stringify(item), item])).values()]};
    });
    const factsMarkup = facts.length ? (personGroups.length ? personGroups.map(group => `<details class="person-fact-group" open><summary><strong>${escapeHtml(group.person)}</strong><small>${group.items.length} 条</small></summary><div class="fact-list">${claimRows(group.items)}</div></details>`).join('') : `<div class="fact-list">${claimRows(facts)}</div>`) : '';
    const claimGroups = [
      facts.length ? `<section class="claim-group" data-claim-type="fact"><h4 class="claim-group-title"><span>事实依据</span><small>${facts.length} 条</small></h4>${factsMarkup}</section>` : '',
      inferences.length ? `<section class="claim-group" data-claim-type="inference"><h4 class="claim-group-title"><span>研判推断</span><small>${inferences.length} 条</small></h4><div class="fact-list">${claimRows(inferences, true)}</div></section>` : ''
    ].filter(Boolean);
    return `<article class="turn" data-analysis-id="${escapeHtml(turn.id)}">
      <div class="question">${escapeHtml(turn.question)}</div>
      <div class="answer"><header class="result-heading"><h3>${escapeHtml(result.judgement_level || '研判结果')}</h3><span class="confidence-badge">置信度 ${escapeHtml(result.confidence || '低')}</span></header>
        <section class="judgement-box">
          <div class="claim-groups">${claimGroups.length ? claimGroups.join('') : '<p class="empty-state">没有可引用的结构化主张</p>'}</div>
        </section></div></article>`;
  }).join('') + pendingTurnMarkup();
  const latest = state.turns[state.turns.length - 1];
  if (latest) {
    state.analysis = {id: latest.id, ...latest.result};
    renderEvidence();
    setReviewEnabled(true);
  } else {
    state.analysis = null;
    renderEvidence();
    setReviewEnabled(false);
  }
  scrollTurnsToEnd();
}

function setReviewEnabled(enabled) {
  $$('[data-review]').forEach(button => { button.disabled = !enabled; });
}

function evidenceItems() {
  if (!state.analysis) return [];
  const support = (state.analysis.evidence || []).map(item => ({...item, kind:'support'}));
  const counter = (state.analysis.counter_evidence || []).map(item => ({...item, kind:'counter'}));
  return state.evidenceKind === 'support' ? support : state.evidenceKind === 'counter' ? counter : [...support, ...counter];
}

function renderEvidence() {
  const support = state.analysis?.evidence || [];
  const counter = state.analysis?.counter_evidence || [];
  const all = [...support, ...counter];
  $('[data-evidence-total]').textContent = `${all.length} ITEMS`;
  $('[data-evidence-kind="support"]').textContent = `支持证据 ${support.length}`;
  $('[data-evidence-kind="counter"]').textContent = `相反证据 ${counter.length}`;
  $('[data-evidence-kind="all"]').textContent = `全部候选 ${all.length}`;
  const items = evidenceItems();
  $('[data-evidence-list]').innerHTML = items.length ? items.map(item => `<article class="evidence ${item.kind === 'counter' ? 'counter' : ''}" data-message-id="${escapeHtml(item.message_id)}">
    <header class="evidence-meta"><span>${escapeHtml(shortId(item.message_id))}</span><span>${escapeHtml(formatTime(item.sent_at))}</span><span class="score">${Number(item.score || 0).toFixed(3)}</span></header>
    <div class="evidence-message"><strong>${escapeHtml(item.sender_id || '未知账号')}</strong><br>${escapeHtml(item.content)}${item.ip_address ? `<br>IP：<mark>${escapeHtml(item.ip_address)}</mark>` : ''}</div>
    <button class="source-link" data-source="${escapeHtml(item.message_id)}">查看群聊原文</button></article>`).join('') : '<p class="empty-state">当前分类没有证据</p>';
}

async function loadGroups() {
  if (!state.currentCase) return;
  state.groups = await api(`/cases/${state.currentCase.id}/groups${materialQuery()}`);
  await syncAnalysisLabels();
  renderGroups();
  const visible = visibleGroups();
  const preferred = visible.find(item => item.group_id === state.activeGroup)?.group_id || visible[0]?.group_id;
  if (preferred) await selectGroup(preferred);
  else {
    state.activeGroup = null;
    state.groupMessages = [];
    renderRawMessages();
  }
}

function visibleGroups() {
  return state.groups.filter(group => state.showHiddenGroups || !state.chatLabels.groups[group.group_id]?.hidden);
}

function groupTypeOf(group) {
  const saved = state.chatLabels.groups[group.group_id];
  if (saved?.type_source === 'manual') return (saved.type || '').trim();
  return (saved?.type || group.inferred_type || '').trim();
}

function groupTypeLabel(group) {
  return groupTypeOf(group) || '未识别，可手动填写';
}

function senderName(senderId) {
  if (!senderId) return '未知账号';
  const saved = state.chatLabels.senders[senderId];
  if (saved?.name) return saved.name;
  const person = state.persons.find(item => item.wechat_id === senderId);
  return person?.name || person?.wx_nickname || senderId;
}

function senderInitial(senderId) {
  const name = senderName(senderId);
  return (name || '?').slice(0, 1).toUpperCase();
}

async function loadChatWorkspace() {
  if (!state.currentCase) {
    state.chatLabels = {groups: {}, senders: {}};
    state.persons = [];
    return;
  }
  const [labels, persons] = await Promise.all([
    api(`/cases/${state.currentCase.id}/chat-labels`),
    api(`/cases/${state.currentCase.id}/persons${materialQuery()}`),
  ]);
  state.chatLabels = {groups: labels.groups || {}, senders: labels.senders || {}};
  state.persons = persons || [];
}

async function persistChatLabels() {
  if (!state.currentCase) return;
  state.chatLabels = await api(`/cases/${state.currentCase.id}/chat-labels`, jsonOptions('PUT', state.chatLabels));
}

async function syncAnalysisLabels() {
  if (!state.currentCase) return;
  let changed = false;
  for (const group of state.groups) {
    const saved = state.chatLabels.groups[group.group_id] || {};
    if (!group.inferred_type || saved.type_source === 'manual') continue;
    const nextType = group.inferred_type.trim();
    if (saved.type === nextType) continue;
    state.chatLabels.groups[group.group_id] = {...saved, type: nextType, type_source: 'analysis', hidden: Boolean(saved.hidden)};
    changed = true;
  }
  for (const person of state.persons) {
    if (!person.wechat_id || !person.name) continue;
    const saved = state.chatLabels.senders[person.wechat_id] || {};
    if (saved.name_source === 'manual' || saved.name === person.name) continue;
    state.chatLabels.senders[person.wechat_id] = {name: person.name, name_source: 'analysis'};
    changed = true;
  }
  if (changed) await persistChatLabels();
}

function focusKeywordList() {
  if (!state.currentConversation?.analysis_enabled) return [];
  const seen = [];
  for (const part of String(state.currentCase?.keywords || '').split(/[,，、;；\s]+/)) {
    const word = part.trim();
    if (word.length >= 2 && !seen.includes(word)) seen.push(word);
  }
  return seen.slice(0, 30);
}

function messageHitsFocus(content) {
  const text = String(content || '').toLowerCase();
  return focusKeywordList().some(word => text.includes(word.toLowerCase()));
}

function renderGroups() {
  const hiddenCount = state.groups.filter(group => state.chatLabels.groups[group.group_id]?.hidden).length;
  const groups = visibleGroups();
  const focusCount = groups.filter(group => group.focus).length;
  const countLabel = hiddenCount ? `${groups.length} 个会话 · 已隐藏 ${hiddenCount}` : `${groups.length} 个会话`;
  $('[data-group-count]').textContent = focusCount ? `${countLabel} · 重点 ${focusCount}` : countLabel;
  const toggle = $('[data-toggle-hidden-groups]');
  if (toggle) toggle.textContent = state.showHiddenGroups ? '收起已隐藏' : (hiddenCount ? `显示已隐藏（${hiddenCount}）` : '显示已隐藏');
  $('.group-list-items').innerHTML = groups.length ? groups.map(group => {
    const hidden = Boolean(state.chatLabels.groups[group.group_id]?.hidden);
    const type = groupTypeOf(group);
    const terms = Array.isArray(group.focus_terms) ? group.focus_terms.filter(Boolean) : [];
    const people = Array.isArray(group.focus_people) ? group.focus_people.filter(Boolean) : [];
    const caseReasons = Array.isArray(group.focus_case) ? group.focus_case.filter(Boolean) : [];
    const focusDetail = [people.join('、'), terms.join('、'), caseReasons.join('、')].filter(Boolean).join(' · ');
    const focusMark = group.focus ? `<em class="group-focus-mark">重点${focusDetail ? ` · ${escapeHtml(focusDetail)}` : ''}</em>` : '';
    const owners = Array.isArray(group.wechat_owners) ? group.wechat_owners.filter(Boolean) : [];
    const ownerMark = `<em class="group-owner-mark" data-known="${owners.length ? 'true' : 'false'}">${escapeHtml(owners.length ? owners.join('、') : '来源未识别')}</em>`;
    const typeLine = type ? `<small>${escapeHtml(type)}</small>` : '';
    return `<article class="group-chat-item" data-chat-id="${escapeHtml(group.group_id)}" data-active="${group.group_id === state.activeGroup}" data-hidden="${hidden}" data-focus="${group.focus ? 'true' : 'false'}">
      <button class="group-chat-main" type="button" data-select-chat="${escapeHtml(group.group_id)}">
        <span class="chat-avatar">${escapeHtml((type || group.group_id).slice(0, 1).toUpperCase())}</span>
        <span class="group-chat-copy"><strong class="group-type-label">${escapeHtml(group.group_id)}</strong>${typeLine}${ownerMark}${focusMark}<small>${escapeHtml(group.latest_content || '暂无消息')}</small></span>
      </button>
      <div class="group-chat-actions">
        <button class="button icon-only" type="button" data-edit-group-type="${escapeHtml(group.group_id)}" aria-label="修改群名称"><svg class="icon compact"><use href="#i-edit"/></svg></button>
        <button class="button icon-only" type="button" data-hide-group="${escapeHtml(group.group_id)}" aria-label="${hidden ? '取消隐藏该群' : '隐藏该群'}"><svg class="icon compact"><use href="#${hidden ? 'i-unread' : 'i-archive'}"/></svg></button>
      </div>
    </article>`;
  }).join('') : '<p class="empty-state">当前案件没有群聊原始数据</p>';
}

async function selectGroup(groupId, quiet = false) {
  if (!state.currentCase || !groupId) return;
  state.activeGroup = groupId;
  const list = $('.group-list-items');
  const pane = $('[data-chat-messages]');
  const listTop = list?.scrollTop || 0;
  const paneTop = pane?.scrollTop || 0;
  const params = new URLSearchParams({group_id: groupId, limit:'5000'});
  if (state.currentConversation?.id) params.set('conversation_id', state.currentConversation.id);
  const messages = await api(`/cases/${state.currentCase.id}/group-messages?${params}`);
  const changed = messages.length !== state.groupMessages.length || messages.at(-1)?.message_id !== state.groupMessages.at(-1)?.message_id;
  state.groupMessages = messages;
  renderGroups();
  if (list) list.scrollTop = listTop;
  if (changed || !quiet) {
    renderRawMessages();
    if (quiet && pane) pane.scrollTop = paneTop;
  }
}

function fillSenderFilter() {
  const select = $('[data-chat-sender]');
  if (!select) return;
  const current = select.value;
  const ids = [...new Set(state.groupMessages.map(item => item.sender_id).filter(Boolean))];
  select.innerHTML = `<option value="">全部成员</option>${ids.map(id => `<option value="${escapeHtml(id)}">${escapeHtml(senderName(id))}</option>`).join('')}`;
  if ([...select.options].some(option => option.value === current)) select.value = current;
}

function filteredRawMessages() {
  const query = $('[data-chat-input]')?.value.trim().toLowerCase() || '';
  const sender = $('[data-chat-sender]')?.value || '';
  const from = $('[data-chat-from]')?.value;
  const to = $('[data-chat-to]')?.value;
  const fromTime = from ? new Date(from).getTime() : null;
  const toTime = to ? new Date(to).getTime() : null;
  const messages = state.groupMessages.filter(item => {
    if (sender && item.sender_id !== sender) return false;
    if (fromTime || toTime) {
      const time = item.sent_at ? new Date(item.sent_at).getTime() : NaN;
      if (Number.isNaN(time)) return false;
      if (fromTime && time < fromTime) return false;
      if (toTime && time > toTime) return false;
    }
    if (!query) return true;
    return `${senderName(item.sender_id)} ${item.sender_id || ''} ${item.content}`.toLowerCase().includes(query);
  });
  const sign = $('[data-chat-sort]')?.value === 'desc' ? -1 : 1;
  return messages.slice().sort((a, b) => {
    const ta = a.sent_at ? new Date(a.sent_at).getTime() : 0;
    const tb = b.sent_at ? new Date(b.sent_at).getTime() : 0;
    if (ta !== tb) return (ta - tb) * sign;
    return String(a.message_id || '').localeCompare(String(b.message_id || '')) * sign;
  });
}

function renderRawMessages() {
  const group = state.groups.find(item => item.group_id === state.activeGroup);
  const owners = Array.isArray(group?.wechat_owners) ? group.wechat_owners.filter(Boolean) : [];
  const typeLabel = group ? groupTypeOf(group) : '';
  const shown = state.groupMessages.length;
  const truncated = group && group.message_count > shown ? ` · 显示最近 ${shown} 条` : '';
  $('[data-chat-title]').textContent = group ? (typeLabel ? `${group.group_id} · ${typeLabel}` : group.group_id) : '请选择群聊';
  $('[data-chat-members]').textContent = group ? `${owners.length ? owners.join('、') : '来源未识别'} · ${group.member_count} 名成员 · ${group.message_count} 条消息${truncated}` : '0 名成员';
  fillSenderFilter();
  const messages = filteredRawMessages();
  $('[data-chat-messages]').innerHTML = messages.length ? messages.map(message => `<article class="raw-message" data-message-id="${escapeHtml(message.message_id)}" ${message.message_id === state.highlightedSource ? 'data-highlighted="true"' : ''} ${messageHitsFocus(message.content) ? 'data-keyword-hit="true"' : ''}>
    <button class="chat-avatar" type="button" data-edit-sender="${escapeHtml(message.sender_id || '')}" title="点击修改显示名称">${escapeHtml(senderInitial(message.sender_id))}</button><div class="raw-message-content">
      <div class="raw-message-meta">
        <strong class="raw-sender-name">${escapeHtml(senderName(message.sender_id))}</strong>
        <span class="raw-sender-id">${escapeHtml(message.sender_id || '未知账号')}</span>
        <span>${escapeHtml(formatTime(message.sent_at))}</span>
        <span>${escapeHtml(shortId(message.message_id))}</span>
        <span class="raw-sender-ip">IP ${escapeHtml(message.ip_address || '未记录')}</span>
      </div>
      <div class="raw-message-bubble">${escapeHtml(message.content)}</div></div></article>`).join('') : '<p class="empty-state">没有匹配消息</p>';
}

async function editGroupType(groupId) {
  const group = state.groups.find(item => item.group_id === groupId);
  if (!group) return;
  const current = groupTypeOf(group);
  const value = prompt('修改该群的备注名称', current);
  if (value === null) return;
  const saved = state.chatLabels.groups[groupId] || {};
  state.chatLabels.groups[groupId] = {...saved, type: value.trim(), type_source: 'manual', hidden: Boolean(saved.hidden)};
  await persistChatLabels();
  renderGroups();
  renderRawMessages();
}

async function toggleGroupHidden(groupId) {
  const saved = state.chatLabels.groups[groupId] || {};
  const hidden = !saved.hidden;
  state.chatLabels.groups[groupId] = {...saved, type: saved.type || '', type_source: saved.type_source || 'analysis', hidden};
  await persistChatLabels();
  if (hidden && state.activeGroup === groupId) {
    const next = visibleGroups().find(item => item.group_id !== groupId);
    if (next) await selectGroup(next.group_id);
    else {
      state.activeGroup = null;
      state.groupMessages = [];
    }
  }
  renderGroups();
  renderRawMessages();
}

async function editSenderName(senderId) {
  if (!senderId) return showToast('该消息没有发送账号');
  const value = prompt('修改该账号在群聊中的显示名称', senderName(senderId));
  if (value === null) return;
  const name = value.trim();
  if (!name) {
    delete state.chatLabels.senders[senderId];
  } else {
    state.chatLabels.senders[senderId] = {name, name_source: 'manual'};
  }
  await persistChatLabels();
  renderRawMessages();
}

async function loadGraph(force = false, keepView = false) {
  if (!state.currentCase) return renderGraph();
  const caseId = state.currentCase.id;
  const graphKey = `${caseId}:${state.currentConversation?.id || ''}`;
  if (!force && state.graphCaseId === graphKey && state.graph) return renderGraph();
  if (!force && state.graphLoading) return state.graphLoading;
  $('[data-graph-meta]').textContent = '正在生成';
  state.graphLoading = api(`/cases/${caseId}/graph${materialQuery()}`).then(graph => {
    if (state.currentCase?.id !== caseId) return;
    const previousSubject = state.activeGraphSubject;
    const previousProfiles = new Set(state.expandedGraphProfiles);
    state.graph = graph;
    state.graphCaseId = graphKey;
    if (keepView) {
      if (previousSubject && graph.nodes.some(node => node.id === previousSubject)) {
        state.activeGraphSubject = previousSubject;
        state.expandedGraphProfiles = previousProfiles;
      } else {
        const sidebar = graphSidebarSubjects(graph);
        state.activeGraphSubject = sidebar.some(node => node.id === state.activeGraphSubject) ? state.activeGraphSubject : sidebar[0]?.id || graph.nodes[0]?.id || null;
      }
    } else {
      const sidebar = graphSidebarSubjects(graph);
      state.activeGraphSubject = sidebar.some(node => node.id === state.activeGraphSubject) ? state.activeGraphSubject : sidebar[0]?.id || graph.nodes[0]?.id || null;
      state.expandedGraphNodes = new Set(state.activeGraphSubject ? [state.activeGraphSubject] : []);
      state.selectedGraphNode = state.activeGraphSubject;
      state.collapsedGraphGroups.clear();
      state.graphLayouts.clear();
      state.graphViewport = {scale:1, x:0, y:0};
      state.graphUndo = [];
      state.graphFocusNode = null;
    }
    if (keepView && state.activeGraphSubject && !state.expandedGraphNodes.has(state.activeGraphSubject)) {
      state.expandedGraphNodes.add(state.activeGraphSubject);
    }
    renderGraph();
  }).finally(() => { state.graphLoading = null; });
  return state.graphLoading;
}

function graphSubjectRemark(node) {
  return String(node?.description || '').trim() === '本人';
}

function graphDisplayRole(node) {
  return graphSubjectRemark(node) ? 'subject' : 'related';
}

function graphSidebarSubjects(graph = state.graph) {
  return (graph?.nodes || []).filter(graphSubjectRemark);
}

function graphEdgeKind(edge) {
  if (edge?.kind === 'friend') return 'friend';
  if (edge?.inferred || edge?.relation === '同群成员') return 'inferred';
  return 'related';
}

function graphVisibleData() {
  const graph = state.graph;
  if (!graph) return {nodes:[], edges:[]};
  const focus = state.graphFocusNode && graph.nodes.some(node => node.id === state.graphFocusNode) ? state.graphFocusNode : null;
  const filters = state.graphEdgeFilters;
  const allKindsOn = filters.related && filters.inferred && filters.friend;
  const edges = graph.edges.filter(edge => {
    if (!filters[graphEdgeKind(edge)]) return false;
    if (focus && edge.source !== focus && edge.target !== focus) return false;
    return true;
  });
  if (!focus && allKindsOn) return {nodes: graph.nodes, edges};
  const keep = new Set(focus ? [focus] : []);
  edges.forEach(edge => {
    keep.add(edge.source);
    keep.add(edge.target);
  });
  return {nodes: graph.nodes.filter(node => keep.has(node.id)), edges};
}

function graphLayoutKey() {
  return state.currentCase?.id || 'canvas';
}

function resolveGraphNodeId(idOrName) {
  if (!idOrName) return idOrName;
  const nodes = state.graph?.nodes || [];
  if (nodes.some(node => node.id === idOrName)) return idOrName;
  return nodes.find(node => node.name === idOrName)?.id || idOrName;
}

function graphNodeByCreated(created, name) {
  return (state.graph?.nodes || []).find(node => node.person_id === created?.id || node.name === name) || null;
}

function revealGraphNode(nodeId, asSubject = false) {
  const id = resolveGraphNodeId(nodeId);
  if (!id) return;
  state.graphFocusNode = null;
  state.selectedGraphNode = id;
  if (asSubject) state.activeGraphSubject = id;
  state.expandedGraphNodes.add(id);
  const position = state.graphLayouts.get(graphLayoutKey())?.get(id);
  const canvas = $('[data-graph-canvas]');
  const box = canvas?.viewBox?.baseVal;
  if (position && box) {
    const scale = state.graphViewport.scale;
    state.graphViewport.x = box.width / 2 - position.x * scale;
    state.graphViewport.y = box.height / 2 - position.y * scale;
  }
}

function placeNodeBeside(positions, nodeId, besideId) {
  nodeId = resolveGraphNodeId(nodeId);
  besideId = resolveGraphNodeId(besideId);
  if (!nodeId || positions.has(nodeId)) return;
  const origin = (besideId && positions.get(besideId)) || {x:350, y:200};
  let x = origin.x + 180;
  let y = origin.y;
  const taken = [...positions.values()];
  while (taken.some(point => Math.hypot(point.x - x, point.y - y) < 120)) y += 140;
  positions.set(nodeId, {x, y});
}

function viewBoxToLayer(point) {
  return {
    x:(point.x - state.graphViewport.x) / state.graphViewport.scale,
    y:(point.y - state.graphViewport.y) / state.graphViewport.scale,
  };
}

function graphArrowLine(start, end, gap = 38) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy) || 1;
  const trim = Math.min(gap, Math.max(0, length / 2 - 8));
  const ux = dx / length;
  const uy = dy / length;
  return {
    x1: start.x + ux * trim,
    y1: start.y + uy * trim,
    x2: end.x - ux * trim,
    y2: end.y - uy * trim,
  };
}

function renderGraphCanvas() {
  const canvas = $('[data-graph-canvas]');
  const empty = $('[data-graph-empty]');
  const {nodes, edges} = graphVisibleData();
  empty.hidden = nodes.length > 0;
  if (!nodes.length) {
    canvas.innerHTML = '';
    canvas.removeAttribute('viewBox');
    applyGraphViewport();
    renderGraphProfiles();
    return;
  }
  const rootNode = nodes.find(node => node.id === state.activeGraphSubject) || nodes[0];
  const others = nodes.filter(node => node.id !== rootNode.id);
  const rowsPerColumn = 5;
  const rowGap = 140;
  const columnGap = 260;
  const top = 88;
  const layoutKey = graphLayoutKey();
  const positions = state.graphLayouts.get(layoutKey) || new Map();
  if (!positions.has(rootNode.id)) positions.set(rootNode.id, {x:160, y:220});
  if (state.pendingNodePlace?.id) {
    placeNodeBeside(positions, state.pendingNodePlace.id, state.pendingNodePlace.beside);
    state.pendingNodePlace = null;
  }
  others.forEach((node, index) => {
    if (!positions.has(node.id)) positions.set(node.id, {
      x:360 + Math.floor(index / rowsPerColumn) * columnGap,
      y:top + index % rowsPerColumn * rowGap,
    });
  });
  state.graphLayouts.set(layoutKey, positions);
  const xs = [...positions.values()].map(point => point.x);
  const ys = [...positions.values()].map(point => point.y);
  const pad = 160;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - pad;
  const width = Math.max(...xs) - minX + pad;
  const height = Math.max(...ys) - minY + pad;
  const untouched = state.graphViewport.scale === 1 && state.graphViewport.x === 0 && state.graphViewport.y === 0;
  if (untouched) canvas.setAttribute('viewBox', `${minX} ${minY} ${width} ${height}`);
  const edgeMarkup = edges.map(edge => {
    const start = positions.get(edge.source);
    const end = positions.get(edge.target);
    if (!start || !end) return '';
    const kind = graphEdgeKind(edge);
    const inferred = kind === 'inferred';
    const friend = kind === 'friend';
    const nudge = friend ? 6 : 0;
    const line = graphArrowLine({x: start.x, y: start.y + nudge}, {x: end.x, y: end.y + nudge});
    const midX = (start.x + end.x) / 2;
    const midY = (start.y + end.y) / 2 + nudge - 8;
    const label = edge.relation
      ? `<text class="graph-edge-label" data-edge-label data-edge-label-source="${escapeHtml(edge.source)}" data-edge-label-target="${escapeHtml(edge.target)}" data-kind="${kind}" x="${midX}" y="${midY}">${escapeHtml(edge.relation)}</text>`
      : '';
    return `<g class="graph-edge-group" data-graph-edge data-edge-source="${escapeHtml(edge.source)}" data-edge-target="${escapeHtml(edge.target)}" data-relation="${escapeHtml(edge.relation || '')}" data-kind="${kind}">
      <line class="graph-edge" data-edge-source="${escapeHtml(edge.source)}" data-edge-target="${escapeHtml(edge.target)}" data-kind="${kind}" data-mutual="${edge.mutual ? 'true' : 'false'}" data-inferred="${inferred}" x1="${line.x1}" y1="${line.y1}" x2="${line.x2}" y2="${line.y2}"/>
      <line class="graph-edge-hit" data-edge-source="${escapeHtml(edge.source)}" data-edge-target="${escapeHtml(edge.target)}" data-kind="${kind}" x1="${line.x1}" y1="${line.y1}" x2="${line.x2}" y2="${line.y2}"/>
      ${label}
    </g>`;
  }).join('');
  const nodeMarkup = [...nodes].sort((left, right) => Number(state.expandedGraphProfiles.has(left.id)) - Number(state.expandedGraphProfiles.has(right.id))).map(node => {
    const position = positions.get(node.id);
    const hasChildren = state.graph.edges.some(edge => edge.source === node.id);
    const expanded = hasChildren && state.expandedGraphNodes.has(node.id);
    const profileOpen = state.expandedGraphProfiles.has(node.id);
    const displayRole = graphDisplayRole(node);
    const canDelete = displayRole !== 'subject';
    return `<g class="graph-node" data-graph-node="${escapeHtml(node.id)}" data-role="${escapeHtml(displayRole)}" data-expanded="${expanded}" data-profile="${profileOpen}" data-selected="${node.id === state.selectedGraphNode}" role="button" tabindex="0" aria-label="${escapeHtml(node.name)}，${hasChildren ? (expanded ? '点击隐藏关系' : '点击展开关系') : '点击展开或隐藏详情'}" transform="translate(${position.x} ${position.y})">
      <circle class="node-halo" r="44"/><circle class="node-shell" r="34"/>
      <text class="graph-node-name" y="4">${escapeHtml(node.name.length > 6 ? node.name.slice(0, 6) : node.name)}</text>
      <g class="graph-node-action" data-graph-add-related="${escapeHtml(node.id)}" role="button" aria-label="增加关系人">
        <circle class="graph-node-action-hit" cx="46" cy="-6" r="11"/>
        <text class="graph-node-action-label" x="46" y="-1">+</text>
      </g>
      ${canDelete ? `<g class="graph-node-action" data-graph-delete-person="${escapeHtml(node.id)}" role="button" aria-label="删除关系人">
        <circle class="graph-node-action-hit" cx="46" cy="14" r="11"/>
        <text class="graph-node-action-label" x="46" y="19">−</text>
      </g>` : ''}
      <g class="graph-profile-toggle" data-graph-profile="${escapeHtml(node.id)}" role="button" aria-label="${profileOpen ? '收起信息' : '展开信息'}">
        <rect class="graph-profile-toggle-hit" x="8" y="-42" width="48" height="22" rx="11"/>
        <rect class="graph-profile-toggle-shell" x="10" y="-40" width="44" height="18" rx="9"/>
        <text class="graph-profile-toggle-label" x="32" y="-27">${profileOpen ? '收' : '信息'}</text>
      </g>
      <g class="graph-link-handle" data-graph-link="${escapeHtml(node.id)}" role="button" aria-label="拉线确定关系">
        <circle class="graph-link-hit" cx="0" cy="34" r="10"/>
        <circle class="graph-link-dot" cx="0" cy="34" r="4"/>
      </g>
    </g>`;
  }).join('');
  canvas.innerHTML = `<defs><marker id="graph-arrow" markerUnits="userSpaceOnUse" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="16" markerHeight="16" orient="auto-start-reverse"><path d="M1 1.4 L11 6 L1 10.6 Z"/></marker></defs><g data-graph-layer>${edgeMarkup}${nodeMarkup}</g><line class="graph-link-preview" data-graph-link-preview visibility="hidden" x1="0" y1="0" x2="0" y2="0"/>`;
  applyGraphViewport();
  renderGraphProfiles();
}

const graphZoomLimits = {min:0.5, max:2.5, step:0.15};

function graphSvgPoint(event) {
  const canvas = $('[data-graph-canvas]');
  const point = canvas.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(canvas.getScreenCTM().inverse());
}

function applyGraphViewport() {
  const layer = $('[data-graph-layer]');
  const level = $('[data-graph-zoom-level]');
  if (layer) {
    const {scale, x, y} = state.graphViewport;
    layer.setAttribute('transform', `translate(${x} ${y}) scale(${scale})`);
  }
  if (level) level.textContent = `${Math.round(state.graphViewport.scale * 100)}%`;
  positionGraphProfiles();
}

function zoomGraph(direction, anchor = null) {
  const viewport = state.graphViewport;
  const nextScale = clamp(viewport.scale + direction * graphZoomLimits.step, graphZoomLimits.min, graphZoomLimits.max);
  if (nextScale === viewport.scale) return;
  const viewBox = $('[data-graph-canvas]').viewBox.baseVal;
  const focus = anchor || {x:viewBox.x + viewBox.width / 2, y:viewBox.y + viewBox.height / 2};
  const localX = (focus.x - viewport.x) / viewport.scale;
  const localY = (focus.y - viewport.y) / viewport.scale;
  state.graphViewport = {scale:nextScale, x:focus.x - localX * nextScale, y:focus.y - localY * nextScale};
  applyGraphViewport();
}

function updateDraggedGraphNode(nodeId) {
  const positions = state.graphLayouts.get(graphLayoutKey());
  const position = positions?.get(nodeId);
  if (!position) return;
  const canvas = $('[data-graph-canvas]');
  const node = Array.from(canvas.querySelectorAll('[data-graph-node]')).find(item => item.dataset.graphNode === nodeId);
  if (node) node.setAttribute('transform', `translate(${position.x} ${position.y})`);
  canvas.querySelectorAll('.graph-edge, .graph-edge-hit').forEach(edge => {
    if (edge.dataset.edgeSource !== nodeId && edge.dataset.edgeTarget !== nodeId) return;
    const start = positions.get(edge.dataset.edgeSource);
    const end = positions.get(edge.dataset.edgeTarget);
    if (!start || !end) return;
    const nudge = edge.dataset.kind === 'friend' ? 6 : 0;
    const line = graphArrowLine({x: start.x, y: start.y + nudge}, {x: end.x, y: end.y + nudge});
    edge.setAttribute('x1', line.x1);
    edge.setAttribute('y1', line.y1);
    edge.setAttribute('x2', line.x2);
    edge.setAttribute('y2', line.y2);
  });
  canvas.querySelectorAll('[data-edge-label]').forEach(label => {
    if (label.dataset.edgeLabelSource !== nodeId && label.dataset.edgeLabelTarget !== nodeId) return;
    const start = positions.get(label.dataset.edgeLabelSource);
    const end = positions.get(label.dataset.edgeLabelTarget);
    if (!start || !end) return;
    label.setAttribute('x', (start.x + end.x) / 2);
    label.setAttribute('y', (start.y + end.y) / 2 + (label.dataset.kind === 'friend' ? 6 : 0) - 8);
  });
  positionGraphProfiles();
}

function resetGraphView() {
  const layoutKey = graphLayoutKey();
  if (layoutKey) state.graphLayouts.delete(layoutKey);
  state.graphViewport = {scale:1, x:0, y:0};
  renderGraphCanvas();
}

function graphProfileValue(value) {
  return String(value || '').trim() || '未登记';
}

function graphProfileFields(node) {
  const accounts = (node.accounts || []).filter(item => item.wechat_id || item.wechat_alias || item.original_wechat_id);
  const fallback = accounts.length ? accounts : [{
    wechat_id: node.wechat_id,
    wechat_alias: node.wechat_alias,
    original_wechat_id: node.original_wechat_id,
    nickname: node.wx_nickname,
  }];
  const fields = [
    ['姓名', graphProfileValue(node.name)],
    ['性别', graphProfileValue(node.gender)],
    ['身份证号', graphProfileValue(node.id_card)],
    ['快递地址', graphProfileValue(node.delivery_address)],
    ['外卖地址', graphProfileValue(node.takeout_address)],
  ];
  fallback.forEach((account, index) => {
    const prefix = fallback.length > 1 ? `账号${index + 1} ` : '';
    fields.push(
      [`${prefix}微信ID`, graphProfileValue(account.wechat_id)],
      [`${prefix}微信号`, graphProfileValue(account.wechat_alias)],
      [`${prefix}原始微信号`, graphProfileValue(account.original_wechat_id)],
      [`${prefix}微信昵称`, graphProfileValue(account.nickname || node.wx_nickname)],
    );
  });
  fields.push(['手机号码', graphProfileValue(node.phone)]);
  return fields;
}

function graphNodeById(nodeId) {
  return (state.graph?.nodes || []).find(node => node.id === nodeId) || null;
}

function graphRelationBetween(leftId, rightId) {
  return (state.graph?.edges || []).find(edge =>
    (edge.source === leftId && edge.target === rightId) || (edge.source === rightId && edge.target === leftId)
  ) || null;
}

function graphProfileMarkup(node) {
  const rows = graphProfileFields(node).map(([label, value]) => `<p><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></p>`).join('');
  const relation = graphRelationBetween(state.activeGraphSubject, node.id);
  const relationRow = relation && node.id !== state.activeGraphSubject
    ? `<p><span>与当前当事人关系</span><strong>${escapeHtml(relation.relation)}</strong></p>`
    : '';
  return `<aside class="graph-profile-card" data-graph-profile-card="${escapeHtml(node.id)}" aria-label="${escapeHtml(node.name)} 人员资料">
    ${relationRow}${rows}
    <div class="graph-profile-actions">
      <button class="button" type="button" data-graph-edit-person>修改信息</button>
      ${node.id !== state.activeGraphSubject ? `<button class="button" type="button" data-graph-edit-relation>修改关系</button>` : ''}
    </div>
  </aside>`;
}

function graphNodeScreenPoint(nodeEl, localX, localY) {
  const canvas = $('[data-graph-canvas]');
  const ctm = nodeEl.getScreenCTM();
  if (!canvas || !ctm) return null;
  const point = canvas.createSVGPoint();
  point.x = localX;
  point.y = localY;
  return point.matrixTransform(ctm);
}

function positionGraphProfiles() {
  const wrap = $('[data-graph-canvas-wrap]');
  const canvas = $('[data-graph-canvas]');
  if (!wrap || !canvas) return;
  const wrapRect = wrap.getBoundingClientRect();
  const pad = 12;
  wrap.querySelectorAll('[data-graph-profile-card]').forEach(card => {
    const nodeEl = canvas.querySelector(`[data-graph-node="${CSS.escape(card.dataset.graphProfileCard)}"]`);
    if (!nodeEl) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    const right = graphNodeScreenPoint(nodeEl, 46, -40);
    if (!right) return;
    const width = card.offsetWidth;
    const height = card.offsetHeight;
    let x = right.x - wrapRect.left;
    let y = right.y - wrapRect.top;
    if (x + width > wrapRect.width - pad) {
      const left = graphNodeScreenPoint(nodeEl, -46, -40);
      if (left) x = left.x - wrapRect.left - width;
    }
    x = Math.min(Math.max(pad, x), Math.max(pad, wrapRect.width - width - pad));
    y = Math.min(Math.max(pad, y), Math.max(pad, wrapRect.height - height - pad));
    card.style.insetInlineStart = `${x}px`;
    card.style.insetBlockStart = `${y}px`;
  });
}

function renderGraphProfiles() {
  const layer = $('[data-graph-profile-layer]');
  if (!layer) return;
  const scrolls = new Map();
  layer.querySelectorAll('[data-graph-profile-card]').forEach(card => {
    scrolls.set(card.dataset.graphProfileCard, card.scrollTop);
  });
  const nodes = (state.graph?.nodes || []).filter(node => state.expandedGraphProfiles.has(node.id));
  layer.innerHTML = nodes.map(graphProfileMarkup).join('');
  const place = () => {
    positionGraphProfiles();
    layer.querySelectorAll('[data-graph-profile-card]').forEach(card => {
      const top = scrolls.get(card.dataset.graphProfileCard);
      if (top) card.scrollTop = top;
    });
  };
  place();
  requestAnimationFrame(place);
}

function toggleGraphProfile(nodeId) {
  if (state.expandedGraphProfiles.has(nodeId)) state.expandedGraphProfiles.clear();
  else state.expandedGraphProfiles = new Set([nodeId]);
  state.selectedGraphNode = nodeId;
  renderGraph();
}

function graphPersonDialog() {
  return $('[data-graph-person-dialog]');
}

function fillGraphPersonForm(node) {
  $('[data-graph-person-id]').value = node?.person_id || '';
  $('[data-graph-person-name]').value = node?.name || '';
  $('[data-graph-person-gender]').value = node?.gender || '';
  $('[data-graph-person-id-card]').value = node?.id_card || '';
  $('[data-graph-person-delivery]').value = node?.delivery_address || '';
  $('[data-graph-person-takeout]').value = node?.takeout_address || '';
  $('[data-graph-person-wechat-id]').value = node?.wechat_id || '';
  $('[data-graph-person-wechat-alias]').value = node?.wechat_alias || '';
  $('[data-graph-person-wechat-original]').value = node?.original_wechat_id || '';
  $('[data-graph-person-nickname]').value = node?.wx_nickname || '';
  $('[data-graph-person-phone]').value = node?.phone || '';
}

function setGraphPersonMode(mode) {
  const title = {edit:'修改人员信息', add:'增加关系人', relation:'修改关系', subject:'新增当事人'}[mode] || '修改人员信息';
  const hint = {edit:'保存后立即更新当前人员资料。', add:'新关系人会连到当前人物。', relation:'修改两人之间的关系名称。', subject:'新增后会出现在左侧当事人栏，可再上传关系表和微信好友表。'}[mode];
  $('[data-graph-person-dialog] h2').textContent = title;
  $('[data-graph-person-hint]').textContent = hint;
  $('[data-graph-person-owner-field]').hidden = mode !== 'add';
  $('[data-graph-person-relation-field]').hidden = mode === 'edit' || mode === 'subject';
  $('[data-graph-person-profile-fields]').hidden = mode === 'relation';
  $('[data-graph-person-name]').required = mode !== 'relation';
  $('[data-graph-person-relation]').required = mode !== 'edit' && mode !== 'subject';
  graphPersonDialog().dataset.mode = mode;
}

function openGraphPersonDialog(mode, nodeId, edge = null) {
  const node = graphNodeById(nodeId);
  if (mode === 'subject') {
    setGraphPersonMode('subject');
    fillGraphPersonForm(null);
    $('[data-graph-person-id]').value = '';
    $('[data-graph-person-name]').value = '';
    $('[data-graph-person-gender]').value = '';
    $('[data-graph-person-id-card]').value = '';
    $('[data-graph-person-delivery]').value = '';
    $('[data-graph-person-takeout]').value = '';
    $('[data-graph-person-wechat-id]').value = '';
    $('[data-graph-person-wechat-alias]').value = '';
    $('[data-graph-person-wechat-original]').value = '';
    $('[data-graph-person-nickname]').value = '';
    $('[data-graph-person-phone]').value = '';
    graphPersonDialog().showModal();
    $('[data-graph-person-name]').focus();
    return;
  }
  if (!node && mode !== 'relation') return;
  if (mode === 'edit' && !node.person_id) return showToast('该节点还没有人员档案，请先增加为关系人');
  setGraphPersonMode(mode);
  fillGraphPersonForm(node);
  const subject = graphNodeById(state.activeGraphSubject);
  if (mode === 'add') {
    $('[data-graph-person-id]').value = '';
    $('[data-graph-person-name]').value = '';
    $('[data-graph-person-gender]').value = '';
    $('[data-graph-person-id-card]').value = '';
    $('[data-graph-person-delivery]').value = '';
    $('[data-graph-person-takeout]').value = '';
    $('[data-graph-person-wechat-id]').value = '';
    $('[data-graph-person-wechat-alias]').value = '';
    $('[data-graph-person-wechat-original]').value = '';
    $('[data-graph-person-nickname]').value = '';
    $('[data-graph-person-phone]').value = '';
    $('[data-graph-person-owner]').value = node.name;
    $('[data-graph-person-relation]').value = '关系人';
    $('[data-graph-person-source]').value = node.name;
    $('[data-graph-person-target]').value = '';
  } else if (mode === 'relation') {
    const sourceNode = graphNodeById(edge?.source || state.activeGraphSubject);
    const targetNode = graphNodeById(edge?.target || nodeId);
    $('[data-graph-person-source]').value = sourceNode?.name || '';
    $('[data-graph-person-target]').value = targetNode?.name || '';
    $('[data-graph-person-relation]').value = edge?.relation || graphRelationBetween(sourceNode?.id, targetNode?.id)?.relation || '关系人';
  }
  graphPersonDialog().showModal();
  (mode === 'relation' ? $('[data-graph-person-relation]') : $('[data-graph-person-name]')).focus();
}

async function submitGraphPerson(event) {
  event.preventDefault();
  if (!state.currentCase) return;
  const mode = graphPersonDialog().dataset.mode;
  const personId = $('[data-graph-person-id]').value.trim();
  const payload = {
    name: $('[data-graph-person-name]').value.trim(),
    gender: $('[data-graph-person-gender]').value.trim(),
    id_card: $('[data-graph-person-id-card]').value.trim(),
    delivery_address: $('[data-graph-person-delivery]').value.trim(),
    takeout_address: $('[data-graph-person-takeout]').value.trim(),
    wechat_id: $('[data-graph-person-wechat-id]').value.trim(),
    wechat_alias: $('[data-graph-person-wechat-alias]').value.trim(),
    original_wechat_id: $('[data-graph-person-wechat-original]').value.trim(),
    wx_nickname: $('[data-graph-person-nickname]').value.trim(),
    phone: $('[data-graph-person-phone]').value.trim(),
    owner_name: $('[data-graph-person-owner]').value.trim(),
    relation: $('[data-graph-person-relation]').value.trim(),
  };
  try {
    if (mode === 'edit') {
      if (!personId) return showToast('没有可修改的人员档案');
      await api(`/cases/${state.currentCase.id}/persons/${personId}${materialQuery()}`, jsonOptions('PATCH', payload));
      showToast('人员信息已更新');
    } else if (mode === 'subject') {
      if (!payload.name) return showToast('请填写当事人姓名');
      const body = {...payload};
      delete body.owner_name;
      delete body.relation;
      await api(`/cases/${state.currentCase.id}/persons${materialQuery()}`, jsonOptions('POST', body));
      showToast('当事人已新增');
    } else if (mode === 'add') {
      if (!payload.name || !payload.owner_name) return showToast('请填写姓名和关联当事人');
      await api(`/cases/${state.currentCase.id}/persons${materialQuery()}`, jsonOptions('POST', payload));
      showToast('已增加关系人');
    } else {
      const source = $('[data-graph-person-source]').value.trim();
      const target = $('[data-graph-person-target]').value.trim();
      if (!source || !target || !payload.relation) return showToast('请填写关系');
      await api(`/cases/${state.currentCase.id}/relations${materialQuery()}`, jsonOptions('PUT', {source, target, relation: payload.relation}));
      showToast('关系已更新');
    }
    graphPersonDialog().close();
    await loadGraph(true, true);
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteGraphPerson(nodeId) {
  const node = graphNodeById(nodeId);
  if (!node?.person_id) return showToast('该节点没有人员档案，无法删除');
  if (graphDisplayRole(node) === 'subject') return showToast('当事人请在左侧当事人栏删除');
  if (!confirm(`确定从本案件删除关系人「${node.name}」吗？案件材料不会删除。`)) return;
  try {
    await api(`/cases/${state.currentCase.id}/persons/${node.person_id}${materialQuery()}`, {method:'DELETE'});
    state.expandedGraphProfiles.clear();
    showToast('关系人已删除');
    await loadGraph(true, true);
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteGraphSubject(nodeId) {
  const node = graphNodeById(nodeId);
  if (!node?.person_id) return showToast('该当事人没有人员档案，无法删除');
  try {
    await api(`/cases/${state.currentCase.id}/persons/${node.person_id}${materialQuery({as_subject:'true'})}`, {method:'DELETE'});
    state.expandedGraphProfiles.clear();
    showToast('当事人已删除');
    await loadGraph(true);
  } catch (error) {
    showToast(error.message);
  }
}

function toggleGraphSubjectCreate() {
  if (!state.currentCase) return showToast('请先选择案件');
  state.graphAddingSubject = !state.graphAddingSubject;
  state.graphSubjectDeleteMode = false;
  renderGraph();
  if (state.graphAddingSubject) {
    const input = $('[data-graph-subject-name]');
    if (input) {
      input.value = '';
      input.focus();
    }
  }
}

function toggleGraphSubjectDeleteMode() {
  if (!state.currentCase) return showToast('请先选择案件');
  state.graphSubjectDeleteMode = !state.graphSubjectDeleteMode;
  state.graphAddingSubject = false;
  renderGraph();
}

async function createGraphSubject() {
  if (!state.currentCase) return showToast('请先选择案件');
  const name = $('[data-graph-subject-name]')?.value.trim();
  if (!name) return showToast('请填写当事人姓名');
  try {
    const created = await api(`/cases/${state.currentCase.id}/persons${materialQuery()}`, jsonOptions('POST', {name}));
    state.graphAddingSubject = false;
    state.graphFocusNode = null;
    const beside = state.selectedGraphNode || state.activeGraphSubject;
    state.pendingNodePlace = {id:name, beside};
    pushGraphUndo({kind:'add-person', personId:created.id, asSubject:true, name});
    showToast('当事人已新增到当前画布');
    await loadGraph(true, true);
    const node = graphNodeByCreated(created, name);
    if (node) {
      const positions = state.graphLayouts.get(graphLayoutKey()) || new Map();
      placeNodeBeside(positions, node.id, beside);
      state.graphLayouts.set(graphLayoutKey(), positions);
      revealGraphNode(node.id, true);
      renderGraph();
    }
  } catch (error) {
    showToast(error.message);
  }
}

function graphNodeNearLayer(x, y) {
  const positions = state.graphLayouts.get(graphLayoutKey());
  if (!positions) return null;
  let best = null;
  let bestDist = 44;
  positions.forEach((position, id) => {
    const dist = Math.hypot(position.x - x, position.y - y);
    if (dist <= bestDist) {
      best = id;
      bestDist = dist;
    }
  });
  return best;
}

function setGraphLinkPreview(x1, y1, x2, y2, visible) {
  const line = $('[data-graph-link-preview]');
  if (!line) return;
  line.setAttribute('x1', x1);
  line.setAttribute('y1', y1);
  line.setAttribute('x2', x2);
  line.setAttribute('y2', y2);
  line.setAttribute('visibility', visible ? 'visible' : 'hidden');
}

function hideGraphLinkEditor() {
  state.pendingGraphLink = null;
  const editor = $('[data-graph-link-editor]');
  if (editor) editor.hidden = true;
  syncGraphUndoButton();
}

function hideGraphRelatedCreate() {
  state.addingRelatedTo = null;
  const box = $('[data-graph-related-create]');
  if (box) box.hidden = true;
  syncGraphUndoButton();
}

function hideGraphNodeMenu() {
  const menuBox = $('[data-graph-node-menu]');
  if (menuBox) menuBox.dataset.open = 'false';
  state.graphMenuNode = null;
}

function focusGraphNeighborhood(nodeId) {
  if (!graphNodeById(nodeId)) return;
  state.graphFocusNode = nodeId;
  state.selectedGraphNode = nodeId;
  hideGraphNodeMenu();
  renderGraph();
}

function clearGraphFocus() {
  if (!state.graphFocusNode) return;
  state.graphFocusNode = null;
  renderGraph();
}

const graphTapStamp = {id: '', at: 0};

function handleGraphTap(nodeId) {
  const now = Date.now();
  const marker = nodeId || '';
  const doubled = graphTapStamp.id === marker && now - graphTapStamp.at < 500;
  graphTapStamp.id = marker;
  graphTapStamp.at = now;
  if (doubled) {
    if (nodeId) {
      if (state.graphFocusNode === nodeId) clearGraphFocus();
      else focusGraphNeighborhood(nodeId);
    } else {
      clearGraphFocus();
    }
    return;
  }
  if (nodeId) {
    state.selectedGraphNode = nodeId;
    renderGraph();
  }
}

function openGraphNodeMenu(event, nodeId) {
  const node = graphNodeById(nodeId);
  if (!node) return;
  event.preventDefault();
  state.graphMenuNode = nodeId;
  menu.dataset.open = 'false';
  caseMenu.dataset.open = 'false';
  const menuBox = $('[data-graph-node-menu]');
  if (!menuBox) return;
  positionMenu(menuBox, event.clientX, event.clientY);
}

async function promoteGraphSubject() {
  const node = graphNodeById(state.graphMenuNode);
  hideGraphNodeMenu();
  if (!state.currentCase) return showToast('请先选择案件');
  if (!node?.person_id) return showToast('该人物没有人员档案');
  if (graphDisplayRole(node) === 'subject') return showToast('已经是当事人');
  try {
    await api(`/cases/${state.currentCase.id}/persons/${node.person_id}${materialQuery()}`, jsonOptions('PATCH', {as_subject: true}));
    pushGraphUndo({kind:'promote', personId: node.person_id, previousRemark: node.description || ''});
    showToast('已添加为当事人');
    await loadGraph(true, true);
  } catch (error) {
    showToast(error.message);
  }
}

function pushGraphUndo(entry) {
  state.graphUndo.push(entry);
  syncGraphUndoButton();
}

function syncGraphUndoButton() {
  const button = $('[data-graph-undo]');
  if (!button) return;
  const drafting = state.graphAddingSubject || Boolean(state.addingRelatedTo) || Boolean(state.pendingGraphLink);
  button.disabled = !drafting && state.graphUndo.length === 0;
}

function cancelGraphDrafts() {
  let cancelled = false;
  if (state.graphAddingSubject) {
    state.graphAddingSubject = false;
    cancelled = true;
  }
  if (state.addingRelatedTo) {
    hideGraphRelatedCreate();
    cancelled = true;
  }
  if (state.pendingGraphLink) {
    hideGraphLinkEditor();
    cancelled = true;
  }
  hideGraphNodeMenu();
  if (cancelled) renderGraph();
  return cancelled;
}

async function undoGraphStep() {
  if (!state.currentCase) return showToast('请先选择案件');
  if (cancelGraphDrafts()) return showToast('已取消当前输入');
  const entry = state.graphUndo.pop();
  syncGraphUndoButton();
  if (!entry) return showToast('没有可撤回的操作');
  try {
    if (entry.kind === 'add-person' && entry.personId) {
      await api(`/cases/${state.currentCase.id}/persons/${entry.personId}${materialQuery(entry.asSubject ? {as_subject:'true'} : {})}`, {method:'DELETE'});
      showToast(entry.asSubject ? '已撤回新增当事人' : '已撤回新增关系人');
    } else if (entry.kind === 'relation') {
      await api(`/cases/${state.currentCase.id}/relations${materialQuery()}`, jsonOptions('PUT', {
        source: entry.source,
        target: entry.target,
        relation: entry.previous || '关系人',
        removed: !entry.previous,
      }));
      showToast('已撤回关系连线');
    } else if (entry.kind === 'promote' && entry.personId) {
      await api(`/cases/${state.currentCase.id}/persons/${entry.personId}${materialQuery()}`, jsonOptions('PATCH', {
        remark: entry.previousRemark || '关系人',
      }));
      showToast('已撤回添加当事人');
    } else {
      return showToast('没有可撤回的操作');
    }
    await loadGraph(true, true);
  } catch (error) {
    state.graphUndo.push(entry);
    syncGraphUndoButton();
    showToast(error.message);
  }
}

function openGraphRelatedCreate(nodeId) {
  const node = graphNodeById(nodeId);
  if (!node) return;
  hideGraphLinkEditor();
  state.addingRelatedTo = nodeId;
  state.expandedGraphNodes.add(nodeId);
  const box = $('[data-graph-related-create]');
  const owner = $('[data-graph-related-owner]');
  const name = $('[data-graph-related-name]');
  const relation = $('[data-graph-related-relation]');
  if (owner) owner.textContent = `为 ${node.name} 新增关系人`;
  if (name) name.value = '';
  if (relation) relation.value = '';
  if (box) box.hidden = false;
  name?.focus();
  syncGraphUndoButton();
}

async function createGraphRelated() {
  const owner = graphNodeById(state.addingRelatedTo);
  if (!state.currentCase || !owner) return showToast('请先选择要关联的人物');
  const name = $('[data-graph-related-name]')?.value.trim();
  const relation = $('[data-graph-related-relation]')?.value.trim() || '关系人';
  if (!name) return showToast('请填写关系人姓名');
  try {
    state.expandedGraphNodes.add(owner.id);
    if (state.activeGraphSubject) state.expandedGraphNodes.add(state.activeGraphSubject);
    const created = await api(`/cases/${state.currentCase.id}/persons${materialQuery()}`, jsonOptions('POST', {
      name,
      owner_name: owner.name,
      relation,
    }));
    hideGraphRelatedCreate();
    state.graphFocusNode = null;
    state.pendingNodePlace = {id:name, beside:owner.id};
    pushGraphUndo({kind:'add-person', personId:created.id, asSubject:false, name});
    showToast('关系人已添加到当前画布');
    await loadGraph(true, true);
    const node = graphNodeByCreated(created, name);
    if (node) {
      const positions = state.graphLayouts.get(graphLayoutKey()) || new Map();
      placeNodeBeside(positions, node.id, owner.id);
      state.graphLayouts.set(graphLayoutKey(), positions);
      revealGraphNode(node.id);
      renderGraph();
    }
  } catch (error) {
    showToast(error.message);
  }
}

function openGraphLinkEditor(sourceId, targetId) {
  const source = graphNodeById(sourceId);
  const target = graphNodeById(targetId);
  if (!source || !target) return;
  hideGraphRelatedCreate();
  state.pendingGraphLink = {source: source.name, target: target.name, sourceId, targetId};
  const editor = $('[data-graph-link-editor]');
  const input = $('[data-graph-link-relation]');
  if (editor) editor.hidden = false;
  if (input) {
    input.value = graphRelationBetween(sourceId, targetId)?.relation || '';
    input.focus();
    input.select();
  }
  syncGraphUndoButton();
}

async function saveGraphLink() {
  const pending = state.pendingGraphLink;
  if (!pending || !state.currentCase) return;
  const relation = $('[data-graph-link-relation]')?.value.trim() || '关系人';
  try {
    const previous = graphRelationBetween(pending.sourceId, pending.targetId)?.relation || '';
    await api(`/cases/${state.currentCase.id}/relations${materialQuery()}`, jsonOptions('PUT', {
      source: pending.source,
      target: pending.target,
      relation,
    }));
    hideGraphLinkEditor();
    state.graphFocusNode = null;
    pushGraphUndo({kind:'relation', source:pending.source, target:pending.target, previous});
    showToast('关系已确定');
    await loadGraph(true, true);
    revealGraphNode(pending.targetId || pending.sourceId);
    renderGraph();
  } catch (error) {
    showToast(error.message);
  }
}

function pickSubjectFile() {
  const node = graphNodeById(state.activeGraphSubject);
  if (!node) return showToast('请先选择当事人');
  state.importScope = 'case';
  state.importOwnerName = node.name;
  $('[data-subject-file-input]')?.click();
}

function renderGraphFriends() {
  const picks = $('[data-graph-friend-picks]');
  const resultBox = $('[data-graph-friend-result]');
  if (!picks) return;
  const people = graphSidebarSubjects();
  const names = new Set(people.map(node => node.name));
  [...state.friendPicks].forEach(name => { if (!names.has(name)) state.friendPicks.delete(name); });
  picks.innerHTML = people.length
    ? people.map(node => `<label class="graph-friend-pick"><input type="checkbox" data-friend-pick="${escapeHtml(node.name)}" ${state.friendPicks.has(node.name) ? 'checked' : ''}><span>${escapeHtml(node.name)}</span></label>`).join('')
    : '<p class="empty-state">新增当事人后再勾选筛查</p>';
  if (!resultBox) return;
  const screen = state.friendScreen;
  if (!screen) {
    resultBox.innerHTML = '<p class="empty-state">勾选至少两个人后点击筛查</p>';
    return;
  }
  const pairs = (screen.pairs || []).map(pair => `<article class="graph-friend-pair"><strong>${escapeHtml(pair.left)} 与 ${escapeHtml(pair.right)}</strong><span>${escapeHtml(pair.relation)}</span></article>`).join('');
  const common = (screen.common_friends || []).map(item => {
    const account = [
      item.wechat_id ? `微信ID ${item.wechat_id}` : '',
      item.wechat_alias ? `微信账号 ${item.wechat_alias}` : '',
      item.original_wechat_id ? `原始账号 ${item.original_wechat_id}` : '',
    ].filter(Boolean).join(' · ') || item.wechat_id || item.wechat_alias || item.original_wechat_id || '未登记微信号';
    const owners = Array.isArray(item.shared_by) ? item.shared_by.filter(Boolean) : [];
    const shared = owners.length ? `出现在 ${item.shared_count || owners.length} 人的好友中：${owners.join('、')}` : '';
    return `<article class="graph-friend-common"><strong>${escapeHtml(account)}</strong><span>${escapeHtml(shared || '未登记来源')}</span></article>`;
  }).join('');
  resultBox.innerHTML = `${pairs}${screen.missing?.length ? `<p class="empty-state">未匹配：${screen.missing.map(escapeHtml).join('、')}</p>` : ''}<h3>好友碰撞 ${screen.common_friends?.length || 0}</h3>${common || '<p class="empty-state">未发现两名及以上人员共同的好友</p>'}`;
}

function graphGroupsForSubject() {
  const graph = state.graph;
  const subject = graph?.nodes.find(node => node.id === state.activeGraphSubject);
  if (!graph || !subject) return [];
  if (!subject.wechat_id) return graph.groups;
  const exact = graph.groups.filter(group => group.subject_ids.includes(subject.id) || group.participants.includes(subject.wechat_id));
  return exact.length ? exact : graph.groups;
}

function renderGraphGroups() {
  const groups = graphGroupsForSubject();
  $('[data-graph-group-count]').textContent = `${groups.length} GROUPS`;
  $('[data-graph-groups]').innerHTML = groups.length ? groups.map((group, index) => {
    const collapsed = state.collapsedGraphGroups.has(group.group_id);
    return `<article class="graph-group-card" data-graph-group="${escapeHtml(group.group_id)}" data-collapsed="${collapsed}">
      <button class="graph-group-head" type="button" data-graph-group-toggle="${escapeHtml(group.group_id)}" aria-expanded="${!collapsed}">
        <span class="graph-group-rank">${String(index + 1).padStart(2, '0')}</span><span class="graph-group-title"><strong>${escapeHtml(group.group_id)}</strong><span>${group.member_count} 个活跃账号</span></span><span class="graph-group-activity">${group.message_count} 条</span>
      </button>
      <div class="graph-group-body" ${collapsed ? 'hidden' : ''}>
        <div class="graph-group-admin"><span>群管理员账号</span><strong>${escapeHtml(group.admin_account || '数据未提供')}</strong>${group.admin_inferred && group.admin_account ? '<small>根据消息活跃度推定，需人工复核</small>' : ''}</div>
        <span class="graph-active-title">主要活跃账号</span><div class="graph-active-accounts">${group.active_accounts.map(item => `<span class="graph-account"><span>${escapeHtml(item.account)}</span><strong>${item.message_count}</strong></span>`).join('') || '<span class="empty-state">暂无账号</span>'}</div>
        <button class="button subtle" type="button" data-graph-open-chat="${escapeHtml(group.group_id)}"><svg class="icon compact"><use href="#i-chat"/></svg>查看群聊原文</button>
      </div>
    </article>`;
  }).join('') : '<p class="empty-state">当前当事人没有可关联群聊</p>';
}

function renderGraphDetail() {
  const detail = $('[data-graph-node-detail]');
  const node = state.graph?.nodes.find(item => item.id === state.selectedGraphNode);
  detail.hidden = !node;
  const relation = state.graph?.edges.find(edge => edge.source === state.activeGraphSubject && edge.target === node?.id)?.relation;
  detail.innerHTML = node ? `<strong>${escapeHtml(node.name)}</strong>${relation ? `<span>与当前当事人：${escapeHtml(relation)}</span>` : ''}<span>${escapeHtml(node.wechat_id || '未登记微信账号')}</span><span>${node.relation_count ?? 0} 条直接关系</span><span>${node.message_count} 条群聊消息</span>${node.description ? `<span>${escapeHtml(node.description)}</span>` : ''}` : '';
}

function renderGraph() {
  const graph = state.graph;
  const subjects = graphSidebarSubjects(graph);
  $('[data-graph-subject-count]').textContent = `${subjects.length} 人`;
  $('[data-graph-meta]').textContent = graph ? `${graph.nodes.length} 人物 · ${graph.edges.length} 关系 · ${graph.relation_source}` : '等待读取';
  $('[data-graph-subjects]').innerHTML = subjects.length ? subjects.map(subject => `<article class="graph-subject-item">
      <button class="graph-subject-button" type="button" data-graph-subject="${escapeHtml(subject.id)}"${subject.id === state.activeGraphSubject ? ' aria-current="true"' : ''}><strong>${escapeHtml(subject.name)}</strong><span>${subject.relation_count ?? 0} 条关系${subject.wechat_id ? ` · ${escapeHtml(subject.wechat_id)}` : ''}</span></button>
      ${state.graphSubjectDeleteMode ? `<button class="button icon-only graph-subject-delete" type="button" data-graph-delete-subject="${escapeHtml(subject.id)}" aria-label="删除当事人 ${escapeHtml(subject.name)}">−</button>` : ''}
    </article>`).join('') : '<p class="empty-state">未识别到当事人</p>';
  const uploadHint = $('[data-graph-upload-hint]');
  const uploadCurrent = $('[data-graph-upload-current]');
  const uploadName = $('[data-graph-upload-name]');
  const current = subjects.find(item => item.id === state.activeGraphSubject);
  if (uploadHint) uploadHint.hidden = Boolean(current);
  if (uploadCurrent) uploadCurrent.hidden = !current;
  if (uploadName) uploadName.textContent = current?.name || '';
  $$('[data-graph-upload-subject]').forEach(button => { button.disabled = !current; });
  const createBox = $('[data-graph-subject-create]');
  if (createBox) createBox.hidden = !state.graphAddingSubject;
  const deleteToggle = $('[data-graph-delete-subjects]');
  if (deleteToggle) deleteToggle.setAttribute('aria-pressed', String(state.graphSubjectDeleteMode));
  const relatedBox = $('[data-graph-related-create]');
  if (relatedBox) relatedBox.hidden = !state.addingRelatedTo;
  $$('[data-graph-edge-filter]').forEach(input => {
    const kind = input.dataset.graphEdgeFilter;
    if (kind) input.checked = Boolean(state.graphEdgeFilters[kind]);
  });
  syncGraphUndoButton();
  const expandable = graph?.nodes.filter(node => graph.edges.some(edge => edge.source === node.id)) || [];
  const allExpanded = expandable.length > 0 && expandable.every(node => state.expandedGraphNodes.has(node.id));
  $('[data-graph-toggle-all] span').textContent = allExpanded ? '全部隐藏' : '全部展开';
  renderGraphCanvas();
  renderGraphDetail();
  renderGraphFriends();
  renderGraphGroups();
}

function openNewCaseDialog() {
  const dialog = $('[data-case-dialog]');
  $('[data-case-name-input]').value = '';
  $('[data-case-code-input]').value = '';
  $('[data-case-category-input]').value = '';
  $('[data-case-handler-input]').value = '';
  $('[data-case-unit-input]').value = '';
  $('[data-case-summary-input]').value = '';
  $('[data-case-keywords-input]').value = '';
  dialog.showModal();
  $('[data-case-name-input]').focus();
}

async function createCase(event) {
  event?.preventDefault();
  const name = $('[data-case-name-input]')?.value.trim();
  const code = $('[data-case-code-input]')?.value.trim();
  const category = $('[data-case-category-input]')?.value.trim();
  const handler = $('[data-case-handler-input]')?.value.trim();
  const unit = $('[data-case-unit-input]')?.value.trim();
  const summary = $('[data-case-summary-input]')?.value.trim();
  const keywords = $('[data-case-keywords-input]')?.value.trim();
  if (!name || !code || !category || !handler || !unit || !summary || !keywords) return showToast('请完整填写案件基本信息');
  const item = await api('/cases', jsonOptions('POST', {name, code, category, handler, unit, summary, keywords}));
  item.conversations = [];
  state.cases.unshift(item);
  $('[data-case-dialog]').close();
  renderHomeCases();
  await selectCase(item);
  showToast('案件创建成功');
}

async function createConversation(title = null, caseItem = state.currentCase) {
  if (!caseItem) return showToast('请先创建或选择案件');
  if (caseItem === state.currentCase && !state.hasCaseMaterials) return showToast('请先导入案件材料');
  if (!title) {
    openConversationDialog(caseItem);
    return null;
  }
  const conversation = await api(`/cases/${caseItem.id}/conversations`, jsonOptions('POST', {title}));
  caseItem.conversations.unshift(conversation);
  if (state.currentCase !== caseItem) await selectCase(caseItem);
  await selectConversation(conversation);
  await refreshSetupStatus();
  showToast('研判对话已创建');
  return conversation;
}

function openConversationDialog(caseItem = state.currentCase) {
  if (!caseItem) return showToast('请先创建或选择案件');
  if (caseItem === state.currentCase && !state.hasCaseMaterials) return showToast('请先导入案件材料');
  state.pendingConversationCase = caseItem;
  $('[data-conversation-title]').value = '新研判对话';
  $('[data-conversation-dialog]').showModal();
  $('[data-conversation-title]').select();
}

async function submitConversation(event) {
  event.preventDefault();
  const title = $('[data-conversation-title]').value.trim();
  if (!title) return showToast('请填写对话名称');
  const caseItem = state.pendingConversationCase || state.currentCase;
  $('[data-conversation-dialog]').close();
  await createConversation(title, caseItem);
}

function openImportDialog(scope = 'case') {
  if (!state.currentCase) return showToast('请先选择案件');
  if (scope === 'conversation' && !state.currentConversation) return showToast('请先新建或选择研判对话');
  state.importOwnerName = '';
  state.importScope = scope;
  const title = $('[data-import-dialog] h2');
  const hint = $('[data-import-hint]');
  if (title) title.textContent = scope === 'conversation' ? '导入对话材料' : '导入案件材料';
  if (hint) hint.textContent = scope === 'conversation'
    ? '对话材料只给当前研判对话使用，其他对话看不到。'
    : '案件材料对这个案件下的所有研判对话可用。人工识别可一次选择多个聊天记录、人员信息和微信好友表。';
  $('[data-import-dialog]').showModal();
}

function pickImportMode(mode, scope = state.importScope) {
  const dialog = $('[data-import-dialog]');
  if (dialog?.open) dialog.close();
  if (mode === 'ai') return showToast('AI 识别暂未开放，请使用人工识别');
  if (!state.currentCase) return showToast('请先选择案件');
  state.importOwnerName = '';
  state.importScope = scope || 'case';
  if (state.importScope === 'conversation' && !state.currentConversation) return showToast('请先新建或选择研判对话');
  $('[data-file-input]').click();
}

async function startMaterialAnalysis() {
  if (!state.currentCase) return showToast('请先选择案件');
  if (!state.hasCaseMaterials) return showToast('请先导入案件材料');
  if (!state.currentConversation) return showToast('请先新建研判对话');
  if (state.taskId) return showToast('正在研判，请稍候');
  if (!state.currentConversation.analysis_enabled) {
    const updated = await api(`/conversations/${state.currentConversation.id}`, jsonOptions('PATCH', {analysis_enabled: true}));
    rememberConversation(updated);
    await refreshSetupStatus();
  }
  state.groups = await api(`/cases/${state.currentCase.id}/groups${materialQuery()}`);
  await syncAnalysisLabels();
  renderGroups();
  renderRawMessages();
  const question = $('[data-question]');
  const keywords = (state.currentCase.keywords || '').trim();
  if (question) {
    const focusRule = '满足其中一条就标为重点群，两条都满足也要标出：同一群有两名及以上已导入人员，或者群聊撞上重点关键词、或根据案件信息判断可能相关';
    question.value = keywords
      ? `请根据案件信息和重点关键词（${keywords}）先提取相关群聊内容，再总结人员信息并判断群类型。${focusRule}`
      : `请根据案件信息总结人员信息并判断群类型。${focusRule}`;
  }
  await runAnalysis({preventDefault() {}});
}

async function runAnalysis(event) {
  event.preventDefault();
  if (state.taskId) {
    await api(`/analysis-tasks/${state.taskId}/cancel`, {method:'POST'});
    return showToast('已请求取消研判');
  }
  if (!state.currentCase) return showToast('请先选择案件');
  if (!state.hasCaseMaterials) return showToast('请先导入案件材料');
  const input = $('[data-question]');
  const question = input.value.trim();
  if (!question) return showToast('请输入待验证问题');
  if (!state.currentConversation) return showToast('请先新建研判对话');
  const mode = state.settings?.model?.mode || 'local';
  const payload = {question, conversation_id:state.currentConversation.id, model_mode:mode, limit:10, context_before:2, context_after:2};
  const button = $('[data-analysis-submit]');
  setWorkspaceMode('chat');
  input.value = '';
  showPendingTurn(question, '已发送，正在提交研判任务');
  button.querySelector('.zh-label').textContent = '取消研判';
  try {
    const task = await api(`/cases/${state.currentCase.id}/analysis-tasks`, jsonOptions('POST', payload));
    state.taskId = task.id;
    updatePendingStatus('任务已提交，正在检索材料');
    let current = task;
    while (['queued', 'running'].includes(current.status)) {
      await new Promise(resolve => setTimeout(resolve, 400));
      current = await api(`/analysis-tasks/${task.id}`);
      updatePendingStatus(current.status === 'running' ? '正在生成研判结果' : '排队处理中');
    }
    if (current.status === 'failed') throw new Error(current.error || '研判失败');
    if (current.status === 'cancelled') {
      clearPendingTurn();
      input.value = question;
      renderTurns();
      return showToast('研判已取消');
    }
    clearPendingTurn();
    state.turns = await api(`/conversations/${state.currentConversation.id}/turns`);
    renderTurns();
    if (state.currentCase) {
      state.groups = await api(`/cases/${state.currentCase.id}/groups${materialQuery()}`);
      await syncAnalysisLabels();
      renderGroups();
      renderRawMessages();
    }
    const engine = current.result?.engine;
    showToast(engine === 'case-overview' ? '已返回案件基本信息和文件清单' : '研判完成，等待人工复核');
  } catch (error) {
    clearPendingTurn();
    if (!input.value.trim()) input.value = question;
    renderTurns();
    throw error;
  } finally {
    state.taskId = null;
    button.querySelector('.zh-label').textContent = '开始研判';
  }
}

function openReviewDialog(decision) {
  if (!state.analysis) return;
  state.reviewDecision = decision;
  $('[data-review-title]').textContent = decision;
  const levelField = $('[data-review-level-field]');
  if (levelField) levelField.hidden = decision !== '修改';
  const level = $('[data-review-level]');
  if (level && state.analysis.judgement_level) level.value = state.analysis.judgement_level;
  $('[data-review-comment]').value = '';
  $('[data-review-dialog]')?.showModal();
  $('[data-reviewer]')?.focus();
}

async function submitReview(event) {
  event?.preventDefault();
  const decision = state.reviewDecision;
  if (!state.analysis || !decision) return;
  const reviewer = $('[data-reviewer]')?.value.trim();
  if (!reviewer) return showToast('请填写复核人');
  const comment = $('[data-review-comment]')?.value.trim() || '';
  const payload = {decision, reviewer, comment};
  if (decision === '修改') {
    const levels = ['明确证据','较强迹象','存在迹象但歧义','证据不足','存在相反证据'];
    const judgement_level = $('[data-review-level]')?.value.trim();
    if (!levels.includes(judgement_level)) return showToast('结论等级无效');
    payload.revised_result = {
      judgement_level, claims:state.analysis.claims || [],
      counter_evidence_refs:(state.analysis.counter_evidence || []).map(item => item.message_id),
      alternatives:state.analysis.alternatives || [], confidence:state.analysis.confidence || '低'
    };
  }
  await api(`/analyses/${state.analysis.id}/reviews`, jsonOptions('POST', payload));
  $('[data-review-dialog]')?.close();
  showToast(`${decision}意见已保存`);
}

async function postSourceFile(file) {
  const data = new FormData();
  data.append('file', file);
  const params = new URLSearchParams();
  if (state.importScope === 'conversation' && state.currentConversation) params.set('conversation_id', state.currentConversation.id);
  if (state.importOwnerName) params.set('owner_name', state.importOwnerName);
  const query = params.toString();
  return api(`/cases/${state.currentCase.id}/sources${query ? `?${query}` : ''}`, {method:'POST', body:data});
}

async function uploadFile(file) {
  if (!state.currentCase || !file) return;
  if (state.importScope === 'conversation' && !state.currentConversation) return showToast('请先新建或选择研判对话');
  const result = await postSourceFile(file);
  const ownerLabel = state.importOwnerName ? `（${state.importOwnerName}）` : '';
  const scopeLabel = result.scope === 'conversation' ? '对话材料' : '案件材料';
  showToast(result.duplicate ? `该${scopeLabel}已经导入` : `${scopeLabel}${ownerLabel}已导入 ${result.imported_messages} 条消息、${result.imported_persons || 0} 名人员、${result.imported_friends || 0} 条好友`);
  state.importOwnerName = '';
  await Promise.all([loadGroups(), loadGraph(true, true), refreshSetupStatus()]);
}

async function uploadFiles(fileList) {
  const files = [...(fileList || [])].filter(Boolean);
  if (files.length <= 1) return uploadFile(files[0]);
  if (!state.currentCase) return;
  if (state.importScope === 'conversation' && !state.currentConversation) return showToast('请先新建或选择研判对话');
  const imported = [];
  const failed = [];
  for (const file of files) {
    try {
      imported.push(await postSourceFile(file));
    } catch (error) {
      failed.push(`${file.name}：${error.message}`);
    }
  }
  state.importOwnerName = '';
  if (imported.length) await Promise.all([loadGroups(), loadGraph(true, true), refreshSetupStatus()]);
  const duplicates = imported.filter(item => item.duplicate).length;
  const fresh = imported.length - duplicates;
  const parts = [];
  if (fresh) parts.push(`已导入 ${fresh} 个表格`);
  if (duplicates) parts.push(`${duplicates} 个表格此前已导入`);
  if (failed.length) parts.push(`${failed.length} 个失败：${failed.join('；')}`);
  showToast(parts.join('，') || '没有导入表格');
}

const setSidebarOpen = open => {
  appShell.dataset.sidebarOpen = String(open);
  $('[data-sidebar-toggle]').setAttribute('aria-expanded', String(open));
};
const copyText = async value => {
  try { await navigator.clipboard.writeText(value); showToast('已复制到剪贴板'); }
  catch { showToast('浏览器未授予剪贴板权限'); }
};
const syncMenuLabels = () => {
  const conversation = state.contextConversation || state.currentConversation;
  if (!conversation) return;
  $('[data-pin-label]').textContent = conversation.pinned ? '取消置顶' : '置顶';
  $('[data-unread-label]').textContent = conversation.unread ? '标记为已读' : '标记为未读';
  $('[data-archive-label]').textContent = conversation.archived ? '取消归档' : '归档';
};
const openMenu = (x, y, conversation) => {
  state.contextConversation = conversation;
  syncMenuLabels();
  caseMenu.dataset.open = 'false';
  positionMenu(menu, x, y);
};
const positionMenu = (target, x, y) => {
  target.dataset.open = 'true';
  target.dataset.side = x > innerWidth / 2 ? 'left' : 'right';
  requestAnimationFrame(() => {
    const rem = Number.parseFloat(getComputedStyle(root).fontSize);
    target.style.setProperty('--menu-x', String(Math.max(rem, Math.min(x, innerWidth - target.offsetWidth - rem)) / rem));
    target.style.setProperty('--menu-y', String(Math.max(rem, Math.min(y, innerHeight - target.offsetHeight - rem)) / rem));
  });
};
const openCaseMenu = (x, y, caseItem) => {
  state.contextCase = caseItem;
  $('[data-case-pin-label]').textContent = caseItem.pinned ? '取消置顶' : '置顶';
  $('[data-case-archive-label]').textContent = caseItem.archived ? '恢复案件' : '归档案件';
  menu.dataset.open = 'false';
  positionMenu(caseMenu, x, y);
};

async function runCommand(command, value = '') {
  const conversation = state.contextConversation || state.currentConversation;
  if (!conversation) return;
  let updated;
  if (command === 'rename') {
    const title = prompt('重命名研判对话', conversation.title)?.trim();
    if (!title) return;
    updated = await api(`/conversations/${conversation.id}`, jsonOptions('PATCH', {title}));
  } else if (command === 'pin') updated = await api(`/conversations/${conversation.id}`, jsonOptions('PATCH', {pinned:!conversation.pinned}));
  else if (command === 'unread') updated = await api(`/conversations/${conversation.id}`, jsonOptions('PATCH', {unread:!conversation.unread}));
  else if (command === 'archive') updated = await api(`/conversations/${conversation.id}`, jsonOptions('PATCH', {archived:!conversation.archived}));
  else if (command === 'section') updated = await api(`/conversations/${conversation.id}`, jsonOptions('PATCH', {section:value}));
  else if (command === 'move') {
    const code = prompt('输入目标案件编号')?.trim();
    const target = state.cases.find(item => item.code === code);
    if (!target) return showToast('没有找到目标案件');
    updated = await api(`/conversations/${conversation.id}`, jsonOptions('PATCH', {case_id:target.id}));
  }   else if (command === 'fork') updated = await api(`/conversations/${conversation.id}/fork`, jsonOptions('POST', {}));
  else if (command === 'delete') {
    menu.dataset.open = 'false';
    await deleteConversation(conversation);
    return;
  } else if (command === 'share') return copyText(`${location.origin}/ui#conversation=${conversation.id}`);
  else if (command === 'copy-title') return copyText(conversation.title);
  else if (command === 'copy-id') return copyText(conversation.id);
  else if (command === 'open') return window.open(`${location.origin}/ui#conversation=${conversation.id}`, '_blank', 'noopener');
  menu.dataset.open = 'false';
  await reloadConversations(updated?.id);
  if (!state.currentConversation) {
    state.turns = [];
    renderTurns();
  }
  showToast(command === 'delete' ? '对话已删除' : '操作已保存');
}

async function runCaseCommand(command, value = '') {
  const caseItem = state.contextCase;
  if (!caseItem) return;
  const activeCaseId = state.currentCase?.id;
  const activeConversationId = state.currentConversation?.id;
  if (command === 'pin') {
    await toggleCasePin(caseItem);
    caseMenu.dataset.open = 'false';
    return;
  }
  if (command === 'archive') {
    await toggleCaseArchive(caseItem);
    caseMenu.dataset.open = 'false';
    return;
  }
  else if (command === 'edit') {
    const name = prompt('编辑项目名称', caseItem.name)?.trim();
    if (!name) return;
    await api(`/cases/${caseItem.id}`, jsonOptions('PATCH', {name}));
  } else if (command === 'section') {
    await api(`/cases/${caseItem.id}`, jsonOptions('PATCH', {section: value, archived: value === '已归档'}));
  }
  else if (command === 'open-folder') {
    const result = await api(`/cases/${caseItem.id}/folder/open`, {method:'POST'});
    caseMenu.dataset.open = 'false';
    return showToast(`已打开 ${result.path}`);
  } else if (command === 'archive-chats') await api(`/cases/${caseItem.id}/conversations/archive`, {method:'POST'});
  else if (command === 'remove') {
    if (!confirmDeleteCase(caseItem)) return;
    await api(`/cases/${caseItem.id}`, {method:'DELETE'});
    localStorage.removeItem(`jt-case-note-${caseItem.id}`);
    caseMenu.dataset.open = 'false';
    await loadCases();
    return showToast('案件及数据库相关数据已删除');
  }
  caseMenu.dataset.open = 'false';
  await loadCases(activeCaseId === caseItem.id && command === 'remove' ? null : activeCaseId, activeConversationId);
  showToast(command === 'archive-chats' ? '项目聊天已归档' : '项目设置已保存');
}

function confirmDeleteConversation(conversation) {
  const caseLabel = state.currentCase
    ? `案件「${state.currentCase.name}」（${state.currentCase.code}）`
    : '当前案件';
  return confirm(
    `确定删除研判对话「${conversation.title}」吗？\n\n只删除该对话的研判和复核记录。\n${caseLabel}的导入数据、群聊和人员会保留。\n\n此操作不可恢复。`
  );
}

async function deleteConversation(conversation) {
  if (!conversation || !confirmDeleteConversation(conversation)) return;
  const wasCurrent = state.currentConversation?.id === conversation.id;
  await api(`/conversations/${conversation.id}`, {method:'DELETE'});
  if (wasCurrent) {
    state.currentConversation = null;
    state.turns = [];
    state.analysis = null;
    if (state.currentCase) {
      history.replaceState(null, '', `#case=${state.currentCase.id}`);
      $('[data-case-meta]').textContent = `${state.currentCase.code} / 请选择研判对话`;
    }
    renderTurns();
    setReviewEnabled(false);
  }
  await reloadConversations();
  await Promise.all([loadGroups(), loadGraph(true)]);
  showToast('研判对话已删除，案件数据仍保留');
}

function confirmDeleteCase(caseItem) {
  return confirm(
    `确定删除案件「${caseItem.name}」（${caseItem.code}）吗？\n\n将仅删除该案件编号、案件名称下的资料、聊天、人员和研判记录，以及该案件本地文件夹。\n其他案件即使导入过相同文件也不会受影响。\n\n此操作不可恢复。`
  );
}

async function toggleCasePin(caseItem) {
  const updated = await api(`/cases/${caseItem.id}`, jsonOptions('PATCH', {pinned: !caseItem.pinned}));
  Object.assign(caseItem, updated);
  state.cases.sort((left, right) => Number(right.pinned) - Number(left.pinned) || String(right.created_at || '').localeCompare(String(left.created_at || '')));
  renderHomeCases();
  showToast(caseItem.pinned ? `已置顶「${caseItem.name}」` : `已取消置顶「${caseItem.name}」`);
}

async function toggleCaseArchive(caseItem) {
  const archived = !caseItem.archived;
  const payload = {archived};
  if (archived) payload.section = '已归档';
  else if (caseItem.section === '已归档') payload.section = '默认';
  const updated = await api(`/cases/${caseItem.id}`, jsonOptions('PATCH', payload));
  Object.assign(caseItem, updated);
  renderHomeCases();
  showToast(caseItem.archived ? `已归档「${caseItem.name}」，可在「已归档」中查看` : `已恢复「${caseItem.name}」`);
}

async function deleteCase(caseItem) {
  if (!confirmDeleteCase(caseItem)) return;
  await api(`/cases/${caseItem.id}`, {method:'DELETE'});
  localStorage.removeItem(`jt-case-note-${caseItem.id}`);
  await loadCases();
  showToast(`已删除案件「${caseItem.name}」（${caseItem.code}）及其数据`);
}

const tokenRem = name => Number.parseFloat(getComputedStyle(root).getPropertyValue(name));
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const savedRem = key => { const value = Number.parseFloat(localStorage.getItem(key)); return Number.isFinite(value) ? value : null; };
const setSidebarWidth = (value, persist = false) => {
  const width = clamp(value, tokenRem('--sidebar-min-size'), tokenRem('--sidebar-max-size'));
  root.style.setProperty('--sidebar-size', `${width}rem`);
  sidebarResizer.setAttribute('aria-valuenow', String(Math.round(width)));
  if (persist) localStorage.setItem('jt-sidebar-width-rem', String(width));
};
const railStorageKey = 'jt-notes-width-rem';
const setRailWidth = (value, persist = false) => {
  const width = clamp(value, tokenRem('--rail-min-size'), tokenRem('--rail-max-size'));
  workbench.style.setProperty('--rail-current-size', `${width}rem`);
  railResizer.setAttribute('aria-valuenow', String(Math.round(width)));
  if (persist) localStorage.setItem(railStorageKey, String(width));
};
const restoreRailWidth = () => setRailWidth(savedRem(railStorageKey) ?? tokenRem('--rail-size'));

const noteStorageKey = () => state.currentCase ? `jt-case-note-${state.currentCase.id}` : null;
function loadCaseNote() {
  const editor = $('[data-case-notes]');
  const key = noteStorageKey();
  const local = key ? localStorage.getItem(key) || '' : '';
  editor.disabled = !key;
  editor.value = state.currentCase ? (state.currentCase.notes || local) : '';
  $('[data-note-status]').textContent = state.currentCase ? '已保存' : '请选择案件';
  $('[data-note-status]').dataset.state = 'saved';
  if (state.currentCase && !state.currentCase.notes && local) saveCaseNote();
}

function saveCaseNote() {
  const key = noteStorageKey();
  if (!key || !state.currentCase) return;
  const value = $('[data-case-notes]').value;
  const status = $('[data-note-status]');
  const caseId = state.currentCase.id;
  localStorage.setItem(key, value);
  status.textContent = '正在保存';
  status.dataset.state = 'saving';
  clearTimeout(state.noteTimer);
  state.noteTimer = setTimeout(() => {
    api(`/cases/${caseId}`, jsonOptions('PATCH', {notes: value})).then(updated => {
      if (state.currentCase?.id !== caseId) return;
      state.currentCase.notes = updated.notes || '';
      const listed = state.cases.find(item => item.id === caseId);
      if (listed) listed.notes = state.currentCase.notes;
      status.textContent = '已保存';
      status.dataset.state = 'saved';
    }).catch(error => {
      status.textContent = '保存失败';
      status.dataset.state = 'error';
      showToast(error.message);
    });
  }, 400);
}

function setWorkspaceMode(mode) {
  workbench.dataset.workspaceMode = mode;
  $$('.workspace-mode-tab').forEach(item => item.setAttribute('aria-selected', String(item.dataset.workspaceMode === mode)));
  $$('[data-workspace-view]').forEach(view => { view.hidden = view.dataset.workspaceView !== mode; });
  if (mode === 'graph') {
    loadGraph().catch(error => showToast(error.message));
    requestAnimationFrame(positionGraphProfiles);
  }
}
function bindResizer(handle, getWidth, setWidth, direction, disabledQuery) {
  let drag = null;
  handle.addEventListener('pointerdown', event => {
    if (matchMedia(disabledQuery).matches) return;
    drag = {id:event.pointerId, x:event.clientX, width:getWidth()};
    handle.dataset.dragging = 'true';
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    setWidth(drag.width + direction * (event.clientX - drag.x) / Number.parseFloat(getComputedStyle(root).fontSize));
  });
  const finish = event => {
    if (!drag || drag.id !== event.pointerId) return;
    setWidth(getWidth(), true); handle.dataset.dragging = 'false'; drag = null;
  };
  handle.addEventListener('pointerup', finish);
  handle.addEventListener('pointercancel', finish);
  handle.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight'].includes(event.key) || matchMedia(disabledQuery).matches) return;
    event.preventDefault();
    setWidth(getWidth() + direction * (event.key === 'ArrowRight' ? 1 : -1) * tokenRem('--resize-step'), true);
  });
}

$$('[data-new-case]').forEach(button => button.addEventListener('click', () => openNewCaseDialog()));
$('[data-case-form]').addEventListener('submit', event => createCase(event).catch(error => showToast(error.message)));
$$('[data-case-cancel]').forEach(button => button.addEventListener('click', () => $('[data-case-dialog]').close()));
$('[data-graph-person-form]')?.addEventListener('submit', event => submitGraphPerson(event).catch(error => showToast(error.message)));
$$('[data-graph-person-cancel]').forEach(button => button.addEventListener('click', () => graphPersonDialog()?.close()));
$('[data-case-search]').addEventListener('input', event => {
  state.caseQuery = event.target.value;
  renderHomeCases();
});
$('[data-case-filter]').addEventListener('click', event => {
  const button = event.target.closest('[data-filter]');
  if (!button) return;
  state.caseFilter = button.dataset.filter;
  $$('[data-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  renderHomeCases();
});
$('[data-home-back]').addEventListener('click', () => showHome());
$$('[data-new-conversation]').forEach(button => button.addEventListener('click', () => createConversation().catch(error => showToast(error.message))));
$('[data-conversation-form]').addEventListener('submit', event => submitConversation(event).catch(error => showToast(error.message)));
$$('[data-conversation-cancel]').forEach(button => button.addEventListener('click', () => $('[data-conversation-dialog]').close()));
$('[data-home-settings]').addEventListener('click', () => openSettings('home').catch(error => showToast(error.message)));
$('[data-home-cases]').addEventListener('click', event => {
  const pin = event.target.closest('[data-pin-case]');
  if (pin) {
    event.preventDefault();
    event.stopPropagation();
    const caseItem = state.cases.find(item => item.id === pin.dataset.pinCase);
    if (caseItem) toggleCasePin(caseItem).catch(error => showToast(error.message));
    return;
  }
  const archive = event.target.closest('[data-archive-case]');
  if (archive) {
    event.preventDefault();
    event.stopPropagation();
    const caseItem = state.cases.find(item => item.id === archive.dataset.archiveCase);
    if (caseItem) toggleCaseArchive(caseItem).catch(error => showToast(error.message));
    return;
  }
  const remove = event.target.closest('[data-delete-case]');
  if (remove) {
    event.preventDefault();
    event.stopPropagation();
    const caseItem = state.cases.find(item => item.id === remove.dataset.deleteCase);
    if (caseItem) deleteCase(caseItem).catch(error => showToast(error.message));
    return;
  }
  const card = event.target.closest('[data-home-case]');
  const caseItem = state.cases.find(item => item.id === card?.dataset.homeCase);
  if (caseItem) selectCase(caseItem).catch(error => showToast(error.message));
});
$('[data-home-cases]').addEventListener('contextmenu', event => {
  const card = event.target.closest('[data-home-case], [data-delete-case], [data-pin-case], [data-archive-case], .case-card');
  const id = card?.dataset.homeCase || card?.dataset.deleteCase || card?.dataset.pinCase || card?.dataset.archiveCase;
  const caseItem = state.cases.find(item => item.id === id);
  if (!caseItem) return;
  event.preventDefault();
  openCaseMenu(event.clientX, event.clientY, caseItem);
});
$('[data-upload]').addEventListener('click', () => openImportDialog('case'));
$('[data-upload-conversation]')?.addEventListener('click', () => openImportDialog('conversation'));
$$('[data-import-mode]').forEach(button => button.addEventListener('click', () => pickImportMode(button.dataset.importMode, button.dataset.importScope || state.importScope)));
$('[data-enter-analysis]')?.addEventListener('click', () => startMaterialAnalysis().catch(error => showToast(error.message)));
$$('[data-import-cancel]').forEach(button => button.addEventListener('click', () => $('[data-import-dialog]').close()));
$('[data-file-input]').addEventListener('change', event => uploadFiles(event.target.files).catch(error => showToast(error.message)).finally(() => { event.target.value = ''; }));
$('[data-subject-file-input]')?.addEventListener('change', event => uploadFile(event.target.files[0]).catch(error => showToast(error.message)).finally(() => { event.target.value = ''; }));
$('[data-analysis-form]').addEventListener('submit', event => runAnalysis(event).catch(error => showToast(error.message)));
$('[data-question]').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey) return;
  event.preventDefault();
  $('[data-analysis-form]').requestSubmit();
});
$$('[data-settings-open]').forEach(button => button.addEventListener('click', () => openSettings('case').catch(error => showToast(error.message))));
$$('[data-settings-tab]').forEach(button => button.addEventListener('click', () => {
  $$('[data-settings-tab]').forEach(item => item.removeAttribute('aria-current'));
  button.setAttribute('aria-current', 'page');
  $$('[data-settings-panel]').forEach(panel => { panel.hidden = panel.dataset.settingsPanel !== button.dataset.settingsTab; });
  if (button.dataset.settingsTab === 'usage') refreshUsage().catch(error => showToast(error.message));
}));
$$('[data-load-models]').forEach(button => button.addEventListener('click', () => loadModels(button.dataset.loadModels).catch(error => showToast(error.message))));
$('[data-api-preset-save]').addEventListener('click', () => Promise.resolve(saveApiPreset()).catch(error => showToast(error.message)));
$('[data-api-preset-list]').addEventListener('click', event => {
  const apply = event.target.closest('[data-api-preset-apply]');
  if (apply) return applyApiPreset(apply.dataset.apiPresetApply).catch(error => showToast(error.message));
  const remove = event.target.closest('[data-api-preset-delete]');
  if (remove) deleteApiPreset(remove.dataset.apiPresetDelete);
});
$$('[data-temperature], [data-top-p]').forEach(input => input.addEventListener('input', updateParameterLabels));
$('[data-prompt-add]').addEventListener('click', addPrompt);
$('[data-skill-select-all]').addEventListener('change', event => {
  $$('[data-skill-list] input[type="checkbox"]').forEach(box => { box.checked = event.target.checked; });
  updateSkillSelectAll();
});
$('[data-skill-list]').addEventListener('change', updateSkillSelectAll);
$$('[data-appearance]').forEach(input => input.addEventListener('input', () => {
  const key = input.dataset.appearance;
  state.appearance[key] = input.type === 'checkbox' ? input.checked : (input.type === 'range' ? Number(input.value) : input.value);
  applyAppearance(true);
  syncAppearanceControls();
}));
$('[data-theme-import-button]').addEventListener('click', () => $('[data-theme-import]').click());
$('[data-theme-import]').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const imported = JSON.parse(await file.text());
    state.appearance = {...appearanceDefaults, ...imported};
    applyAppearance(true);
    syncAppearanceControls();
    showToast('主题已导入');
  } catch { showToast('主题文件格式无效'); }
  finally { event.target.value = ''; }
});
$('[data-theme-copy]').addEventListener('click', () => copyText(JSON.stringify(state.appearance, null, 2)));
settingsDialog.addEventListener('close', () => { clearInterval(state.usageTimer); state.usageTimer = null; });
$('[data-settings-form]').addEventListener('submit', event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  if (event.submitter?.value !== 'save') return;
  if (state.settingsScope === 'home' || !state.currentCase) {
    settingsDialog.close();
    return showToast('外观已保存');
  }
  saveSettings().catch(error => showToast(error.message));
});
$('[data-refresh]').addEventListener('click', () => Promise.all([loadGroups(), loadGraph(true), state.currentConversation ? selectConversation(state.currentConversation) : null]).then(() => showToast('数据已刷新')).catch(error => showToast(error.message)));
$$('[data-review]').forEach(button => button.addEventListener('click', () => openReviewDialog(button.dataset.review)));
$('[data-review-form]')?.addEventListener('submit', event => submitReview(event).catch(error => showToast(error.message)));
$$('[data-review-cancel]').forEach(button => button.addEventListener('click', () => $('[data-review-dialog]')?.close()));

$('.case-tree').addEventListener('click', event => {
  const remove = event.target.closest('[data-delete-conversation]');
  if (remove) {
    event.preventDefault();
    event.stopPropagation();
    const conversation = state.cases.flatMap(item => item.conversations || []).find(item => item.id === remove.dataset.deleteConversation);
    if (conversation) deleteConversation(conversation).catch(error => showToast(error.message));
    return;
  }
  const addButton = event.target.closest('[data-new-conversation-case]');
  if (addButton) {
    const caseItem = state.cases.find(item => item.id === addButton.dataset.newConversationCase);
    if (caseItem) createConversation(null, caseItem).catch(error => showToast(error.message));
    return;
  }
  const conversationNode = event.target.closest('[data-conversation-id]');
  if (conversationNode) {
    const conversation = state.cases.flatMap(item => item.conversations).find(item => item.id === conversationNode.dataset.conversationId);
    if (conversation) selectConversation(conversation).catch(error => showToast(error.message));
    return;
  }
  const caseNode = event.target.closest('[data-case-id]');
  const caseItem = state.cases.find(item => item.id === caseNode?.dataset.caseId);
  if (caseItem) selectCase(caseItem, caseItem.conversations[0]).catch(error => showToast(error.message));
});
$('.case-tree').addEventListener('contextmenu', event => {
  const conversationNode = event.target.closest('[data-conversation-id]');
  const caseNode = event.target.closest('[data-case-id]');
  if (!conversationNode && !caseNode) return;
  event.preventDefault();
  if (conversationNode) {
    const conversation = state.cases.flatMap(item => item.conversations).find(item => item.id === conversationNode.dataset.conversationId);
    if (conversation) openMenu(event.clientX, event.clientY, conversation);
  } else {
    const caseItem = state.cases.find(item => item.id === caseNode.dataset.caseId);
    if (caseItem) openCaseMenu(event.clientX, event.clientY, caseItem);
  }
});
menu.addEventListener('click', event => {
  event.stopPropagation();
  const item = event.target.closest('[data-command]');
  if (item) runCommand(item.dataset.command, item.dataset.value).catch(error => showToast(error.message));
});
caseMenu.addEventListener('click', event => {
  event.stopPropagation();
  const item = event.target.closest('[data-case-command]');
  if (item) runCaseCommand(item.dataset.caseCommand, item.dataset.value).catch(error => showToast(error.message));
});
document.addEventListener('click', () => { menu.dataset.open = 'false'; caseMenu.dataset.open = 'false'; hideGraphNodeMenu(); });
$('[data-menu-toggle]').addEventListener('click', event => {
  event.stopPropagation();
  const bounds = event.currentTarget.getBoundingClientRect();
  if (state.currentConversation) openMenu(bounds.right, bounds.bottom, state.currentConversation);
});

$$('.workspace-mode-tab').forEach(button => button.addEventListener('click', () => setWorkspaceMode(button.dataset.workspaceMode)));
$$('[data-evidence-kind]').forEach(button => button.addEventListener('click', () => {
  state.evidenceKind = button.dataset.evidenceKind;
  $$('[data-evidence-kind]').forEach(item => item.setAttribute('aria-selected', String(item === button)));
  renderEvidence();
}));
const openSourceMessage = async event => {
  const source = event.target.closest('[data-source]')?.dataset.source;
  if (!source) return;
  const item = state.turns.flatMap(turn => [...(turn.result?.evidence || []), ...(turn.result?.counter_evidence || [])]).find(row => row.message_id === source);
  if (!item?.group_id) return showToast('该证据没有群聊编号');
  state.highlightedSource = source;
  setWorkspaceMode('raw');
  await selectGroup(item.group_id);
  const message = $(`[data-chat-messages] [data-message-id="${CSS.escape(source)}"]`);
  if (!message) return showToast('原始消息未找到');
  message.dataset.highlighted = 'true';
  message.scrollIntoView({block:'center'});
};
$('[data-evidence-list]').addEventListener('click', event => openSourceMessage(event).catch(error => showToast(error.message)));
$('[data-turns]').addEventListener('click', event => openSourceMessage(event).catch(error => showToast(error.message)));
$('.group-list-items').addEventListener('click', event => {
  const edit = event.target.closest('[data-edit-group-type]');
  if (edit) {
    event.preventDefault();
    return editGroupType(edit.dataset.editGroupType).catch(error => showToast(error.message));
  }
  const hide = event.target.closest('[data-hide-group]');
  if (hide) {
    event.preventDefault();
    return toggleGroupHidden(hide.dataset.hideGroup).catch(error => showToast(error.message));
  }
  const item = event.target.closest('[data-select-chat]');
  if (item) {
    state.highlightedSource = null;
    selectGroup(item.dataset.selectChat).catch(error => showToast(error.message));
  }
});
$('[data-toggle-hidden-groups]')?.addEventListener('click', () => {
  state.showHiddenGroups = !state.showHiddenGroups;
  renderGroups();
});
$('[data-chat-messages]').addEventListener('click', event => {
  const button = event.target.closest('[data-edit-sender]');
  if (button) editSenderName(button.dataset.editSender).catch(error => showToast(error.message));
});
$('[data-chat-form]').addEventListener('submit', event => { event.preventDefault(); renderRawMessages(); });
$('[data-chat-filter-clear]')?.addEventListener('click', () => {
  if ($('[data-chat-sender]')) $('[data-chat-sender]').value = '';
  if ($('[data-chat-from]')) $('[data-chat-from]').value = '';
  if ($('[data-chat-to]')) $('[data-chat-to]').value = '';
  if ($('[data-chat-input]')) $('[data-chat-input]').value = '';
  renderRawMessages();
});
['data-chat-sender', 'data-chat-from', 'data-chat-to', 'data-chat-sort'].forEach(name => {
  $(`[${name}]`)?.addEventListener('change', () => renderRawMessages());
});
$('[data-graph-add-subject]')?.addEventListener('click', toggleGraphSubjectCreate);
$('[data-graph-delete-subjects]')?.addEventListener('click', toggleGraphSubjectDeleteMode);
$('[data-graph-subject-save]')?.addEventListener('click', () => createGraphSubject().catch(error => showToast(error.message)));
$('[data-graph-subject-cancel]')?.addEventListener('click', () => { cancelGraphDrafts(); });
$('[data-graph-subject-name]')?.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    createGraphSubject().catch(error => showToast(error.message));
  }
  if (event.key === 'Escape') cancelGraphDrafts();
});
$('[data-graph-related-save]')?.addEventListener('click', () => createGraphRelated().catch(error => showToast(error.message)));
$('[data-graph-related-cancel]')?.addEventListener('click', () => { hideGraphRelatedCreate(); });
['data-graph-related-name', 'data-graph-related-relation'].forEach(name => {
  $(`[${name}]`)?.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      createGraphRelated().catch(error => showToast(error.message));
    }
    if (event.key === 'Escape') hideGraphRelatedCreate();
  });
});
$('[data-graph-link-save]')?.addEventListener('click', () => saveGraphLink().catch(error => showToast(error.message)));
$('[data-graph-link-cancel]')?.addEventListener('click', () => { hideGraphLinkEditor(); });
$('[data-graph-link-relation]')?.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    saveGraphLink().catch(error => showToast(error.message));
  }
  if (event.key === 'Escape') hideGraphLinkEditor();
});
$('[data-graph-undo]')?.addEventListener('click', () => undoGraphStep().catch(error => showToast(error.message)));
$('[data-graph-edge-filters]')?.addEventListener('change', event => {
  const kind = event.target.dataset.graphEdgeFilter;
  if (!kind) return;
  state.graphEdgeFilters[kind] = event.target.checked;
  renderGraph();
});
$('[data-graph-node-menu]')?.addEventListener('click', event => {
  event.stopPropagation();
  if (event.target.closest('[data-graph-promote-subject]')) promoteGraphSubject().catch(error => showToast(error.message));
});
$('[data-graph-subject-upload]')?.addEventListener('click', event => {
  if (!event.target.closest('[data-graph-upload-subject]')) return;
  pickSubjectFile();
});
$('[data-graph-subjects]').addEventListener('click', event => {
  const deleteId = event.target.closest('[data-graph-delete-subject]')?.dataset.graphDeleteSubject;
  if (deleteId) {
    deleteGraphSubject(deleteId).catch(error => showToast(error.message));
    return;
  }
  const subject = event.target.closest('[data-graph-subject]')?.dataset.graphSubject;
  if (!subject) return;
  state.activeGraphSubject = subject;
  state.selectedGraphNode = subject;
  state.graphFocusNode = null;
  renderGraph();
});
const activateGraphNode = nodeId => handleGraphTap(nodeId);
const graphCanvas = $('[data-graph-canvas]');
graphCanvas.addEventListener('contextmenu', event => {
  const nodeId = event.target.closest('[data-graph-node]')?.dataset.graphNode;
  if (!nodeId) return;
  event.preventDefault();
  const node = graphNodeById(nodeId);
  if (!node?.person_id) return showToast('该人物没有人员档案');
  if (graphDisplayRole(node) === 'subject') return showToast('已经是当事人');
  openGraphNodeMenu(event, nodeId);
});
graphCanvas.addEventListener('click', event => {
  const actionCard = event.target.closest('[data-graph-profile-card]');
  if (actionCard) return;
  if (event.target.closest('[data-graph-link]')) return;
  const addId = event.target.closest('[data-graph-add-related]')?.dataset.graphAddRelated;
  if (addId) {
    openGraphRelatedCreate(addId);
    return;
  }
  const deleteId = event.target.closest('[data-graph-delete-person]')?.dataset.graphDeletePerson;
  if (deleteId) {
    deleteGraphPerson(deleteId);
    return;
  }
  const edge = event.target.closest('[data-graph-edge]');
  if (edge) {
    openGraphPersonDialog('relation', edge.dataset.edgeTarget, {
      source: edge.dataset.edgeSource,
      target: edge.dataset.edgeTarget,
      relation: edge.dataset.relation,
    });
    return;
  }
  const profileId = event.target.closest('[data-graph-profile]')?.dataset.graphProfile;
  if (profileId) {
    toggleGraphProfile(profileId);
    return;
  }
  if (event.detail !== 0) return;
  const nodeId = event.target.closest('[data-graph-node]')?.dataset.graphNode;
  if (nodeId) activateGraphNode(nodeId);
});
graphCanvas.addEventListener('keydown', event => {
  if (!['Enter', ' '].includes(event.key)) return;
  const nodeId = event.target.closest('[data-graph-node]')?.dataset.graphNode;
  if (!nodeId) return;
  event.preventDefault();
  activateGraphNode(nodeId);
});
graphCanvas.addEventListener('wheel', event => {
  if (!state.graph || event.target.closest('[data-graph-profile-card]')) return;
  event.preventDefault();
  zoomGraph(event.deltaY < 0 ? 1 : -1, graphSvgPoint(event));
}, {passive:false});
$('[data-graph-profile-layer]')?.addEventListener('wheel', event => {
  const card = event.target.closest('[data-graph-profile-card]');
  if (!card) return;
  event.preventDefault();
  event.stopPropagation();
  const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
  card.scrollTop += delta;
}, {passive:false});
$('[data-graph-profile-layer]')?.addEventListener('pointerdown', event => event.stopPropagation());
$('[data-graph-profile-layer]')?.addEventListener('click', event => {
  const card = event.target.closest('[data-graph-profile-card]');
  if (!card) return;
  const nodeId = card.dataset.graphProfileCard;
  if (event.target.closest('[data-graph-edit-person]')) openGraphPersonDialog('edit', nodeId);
  else if (event.target.closest('[data-graph-add-related]')) openGraphRelatedCreate(nodeId);
  else if (event.target.closest('[data-graph-edit-relation]')) {
    const edge = graphRelationBetween(state.activeGraphSubject, nodeId);
    openGraphPersonDialog('relation', nodeId, edge ? {source: edge.source, target: edge.target, relation: edge.relation} : null);
  }
  else if (event.target.closest('[data-graph-delete-person]')) deleteGraphPerson(nodeId);
});
window.addEventListener('resize', () => positionGraphProfiles());
graphCanvas.addEventListener('pointerdown', event => {
  if (event.button !== 0 || !state.graph) return;
  if (event.target.closest('[data-graph-profile], [data-graph-profile-card], [data-graph-edge], [data-graph-add-related], [data-graph-delete-person]')) return;
  const point = graphSvgPoint(event);
  const linkId = event.target.closest('[data-graph-link]')?.dataset.graphLink;
  if (linkId) {
    const position = state.graphLayouts.get(graphLayoutKey())?.get(linkId);
    if (!position) return;
    state.graphPointer = {
      id:event.pointerId, kind:'link', nodeId:linkId, start:point, moved:false,
      origin:{x:point.x, y:point.y},
    };
    graphCanvas.dataset.dragging = 'link';
    setGraphLinkPreview(point.x, point.y, point.x, point.y, true);
    graphCanvas.setPointerCapture(event.pointerId);
    return;
  }
  const nodeId = event.target.closest('[data-graph-node]')?.dataset.graphNode || null;
  const position = nodeId ? state.graphLayouts.get(graphLayoutKey())?.get(nodeId) : null;
  state.graphPointer = {
    id:event.pointerId, kind:nodeId ? 'node' : 'pan', nodeId, start:point, moved:false,
    origin:nodeId && position ? {...position} : {...state.graphViewport},
  };
  graphCanvas.dataset.dragging = nodeId ? 'node' : 'pan';
  graphCanvas.setPointerCapture(event.pointerId);
});
graphCanvas.addEventListener('pointermove', event => {
  const pointer = state.graphPointer;
  if (!pointer || pointer.id !== event.pointerId) return;
  const point = graphSvgPoint(event);
  const dx = point.x - pointer.start.x;
  const dy = point.y - pointer.start.y;
  if (Math.hypot(dx, dy) > 8) pointer.moved = true;
  if (pointer.kind === 'link') {
    setGraphLinkPreview(pointer.origin.x, pointer.origin.y, point.x, point.y, true);
    return;
  }
  if (pointer.nodeId) {
    const positions = state.graphLayouts.get(graphLayoutKey());
    positions?.set(pointer.nodeId, {
      x:pointer.origin.x + dx / state.graphViewport.scale,
      y:pointer.origin.y + dy / state.graphViewport.scale,
    });
    updateDraggedGraphNode(pointer.nodeId);
  } else {
    state.graphViewport.x = pointer.origin.x + dx;
    state.graphViewport.y = pointer.origin.y + dy;
    applyGraphViewport();
  }
});
const finishGraphDrag = (event, cancelled = false) => {
  const pointer = state.graphPointer;
  if (!pointer || pointer.id !== event.pointerId) return;
  const activateNode = !cancelled && !pointer.moved && pointer.kind !== 'link' ? pointer.nodeId : null;
  if (pointer.kind === 'link') {
    const point = graphSvgPoint(event);
    const end = viewBoxToLayer(point);
    setGraphLinkPreview(0, 0, 0, 0, false);
    if (!cancelled && pointer.moved) {
      const hit = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-graph-node]')?.dataset.graphNode
        || graphNodeNearLayer(end.x, end.y);
      if (hit && hit !== pointer.nodeId) openGraphLinkEditor(pointer.nodeId, hit);
      else showToast('请拉到另一个人物上');
    }
  }
  state.graphPointer = null;
  delete graphCanvas.dataset.dragging;
  if (graphCanvas.hasPointerCapture(event.pointerId)) graphCanvas.releasePointerCapture(event.pointerId);
  if (activateNode) handleGraphTap(activateNode);
  else if (!cancelled && !pointer.moved) handleGraphTap(null);
};
graphCanvas.addEventListener('pointerup', finishGraphDrag);
graphCanvas.addEventListener('pointercancel', event => finishGraphDrag(event, true));
$$('[data-graph-zoom]').forEach(button => button.addEventListener('click', () => zoomGraph(button.dataset.graphZoom === 'in' ? 1 : -1)));
$('[data-graph-reset]').addEventListener('click', resetGraphView);
$('[data-graph-toggle-all]').addEventListener('click', () => {
  const expandable = state.graph?.nodes.filter(node => state.graph.edges.some(edge => edge.source === node.id)) || [];
  const allExpanded = expandable.length > 0 && expandable.every(node => state.expandedGraphNodes.has(node.id));
  state.expandedGraphNodes = allExpanded ? new Set() : new Set(expandable.map(node => node.id));
  renderGraph();
});
$('[data-graph-friend-picks]')?.addEventListener('change', event => {
  const name = event.target.dataset.friendPick;
  if (!name) return;
  if (event.target.checked) state.friendPicks.add(name);
  else state.friendPicks.delete(name);
});
$('[data-graph-friend-screen]')?.addEventListener('click', () => {
  const names = [...state.friendPicks];
  if (names.length < 2) return showToast('请至少勾选两个人');
  if (!state.currentCase) return showToast('请先选择案件');
  api(`/cases/${state.currentCase.id}/friend-screen${materialQuery()}`, jsonOptions('POST', {names})).then(result => {
    state.friendScreen = result;
    renderGraphFriends();
  }).catch(error => showToast(error.message));
});
$('[data-graph-groups]').addEventListener('click', event => {
  const open = event.target.closest('[data-graph-open-chat]')?.dataset.graphOpenChat;
  if (open) {
    setWorkspaceMode('raw');
    selectGroup(open).catch(error => showToast(error.message));
    return;
  }
  const groupId = event.target.closest('[data-graph-group-toggle]')?.dataset.graphGroupToggle;
  if (!groupId) return;
  if (state.collapsedGraphGroups.has(groupId)) state.collapsedGraphGroups.delete(groupId);
  else state.collapsedGraphGroups.add(groupId);
  renderGraphGroups();
});
$('[data-case-notes]').addEventListener('input', saveCaseNote);

$$('[data-theme-toggle]').forEach(button => button.addEventListener('click', () => {
  state.appearance.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
  applyAppearance(true);
  syncAppearanceControls();
}));
$$('[data-language-toggle]').forEach(button => button.addEventListener('click', () => { root.dataset.i18n = root.dataset.i18n === 'zh' ? 'en' : 'zh'; }));
$('[data-sidebar-toggle]').addEventListener('click', () => setSidebarOpen(appShell.dataset.sidebarOpen !== 'true'));
$('[data-sidebar-close]').addEventListener('click', () => setSidebarOpen(false));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    menu.dataset.open = 'false';
    caseMenu.dataset.open = 'false';
    hideGraphNodeMenu();
    if (workbench.dataset.workspaceMode === 'graph') cancelGraphDrafts();
    setSidebarOpen(false);
  }
  if (event.altKey && event.ctrlKey && event.key.toLowerCase() === 'r') runCommand('rename').catch(error => showToast(error.message));
  if (event.altKey && event.ctrlKey && event.key.toLowerCase() === 'p') runCommand('pin').catch(error => showToast(error.message));
  if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'u') runCommand('unread').catch(error => showToast(error.message));
  if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'a') runCommand('archive').catch(error => showToast(error.message));
});

setSidebarWidth(savedRem('jt-sidebar-width-rem') ?? tokenRem('--sidebar-size'));
try { restoreRailWidth(); } catch {}
if (sidebarResizer) bindResizer(sidebarResizer, () => $('.sidebar').getBoundingClientRect().width / Number.parseFloat(getComputedStyle(root).fontSize), setSidebarWidth, 1, '(max-width: 48rem)');
if (railResizer) bindResizer(railResizer, () => $('.evidence-column').getBoundingClientRect().width / Number.parseFloat(getComputedStyle(root).fontSize), setRailWidth, -1, '(max-width: 64rem)');
setInterval(() => {
  if (workbench.dataset.workspaceMode === 'raw' && state.activeGroup) selectGroup(state.activeGroup, true).catch(() => {});
}, 4000);

if (location.protocol === 'file:') location.replace('http://127.0.0.1:8765/ui');
else loadCases().catch(error => {
  setBackendStatus('后端连接失败', false);
  showHome();
  showToast(error.message);
});
