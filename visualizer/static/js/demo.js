/* Common navigation and guided practice on the real pages of an isolated project. */
(() => {
  const boot = window.KVT_DEMO || {active:false};
  const rawFetch = window.fetch.bind(window);
  window.kvtPath = path => boot.active && path.startsWith('/') && !path.startsWith(boot.prefix + '/') ? boot.prefix + path : path;
  window.startKvtDemo = async button => {
    if (boot.active) { await move('goto', state.step); return; }
    button.disabled = true;
    try {
      const response = await rawFetch('/demo/start', {method:'POST'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      location.assign(data.url);
    } catch (error) { showToast(error.message, 'error'); button.disabled = false; }
  };
  if (!boot.active) return;
  let state = boot.state;
  let busy = false;
  let hintTimer, hintKey;
  let hintTarget = null;
  window.fetch = async (input, options) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    if (url.origin !== location.origin) throw new Error('Внешние запросы в демо отключены');
    const mapped = window.kvtPath(url.pathname) + url.search;
    const response = await rawFetch(input instanceof Request ? new Request(mapped, input) : mapped, options);
    const method = (options?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (method !== 'GET' && response.ok && !url.pathname.includes('/_demo/')) refresh().catch(() => {});
    return response;
  };
  // Dynamic links and file downloads must stay inside the training project too.
  const rebaseLinks = node => {
    if (!node.querySelectorAll) return;
    node.querySelectorAll('a[href],form[action]').forEach(element => {
      const attr = element.tagName === 'FORM' ? 'action' : 'href';
      const value = element.getAttribute(attr);
      if (value?.startsWith('/') && !value.startsWith('//')) element.setAttribute(attr, window.kvtPath(value));
    });
  };
  rebaseLinks(document);
  new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
    if (node.nodeType === 1) {
      if (node.matches('a[href]')) {
        const value = node.getAttribute('href');
        if (value.startsWith('/') && !value.startsWith('//')) node.setAttribute('href', window.kvtPath(value));
      }
      rebaseLinks(node);
    }
  }))).observe(document.body, {childList:true, subtree:true});
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form.action && !new URL(form.action).pathname.startsWith(boot.prefix + '/')) event.preventDefault();
  }, true);
  const rawOpen = window.open.bind(window);
  window.open = (url, target) => {
    const parsed = new URL(url, location.href);
    if (parsed.origin === location.origin) return rawOpen(window.kvtPath(parsed.pathname) + parsed.search, target);
    showToast('В демо открывайте разделы через меню: внешние службы имитируются'); return null;
  };
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[href]');
    if (!anchor) return;
    const parsed = new URL(anchor.href, location.href);
    if (parsed.origin === location.origin) anchor.href = window.kvtPath(parsed.pathname) + parsed.search + parsed.hash;
    else {event.preventDefault(); showToast('Внешние переходы в демо отключены');}
  }, true);
  document.body.classList.add('kvt-demo');
  const banner = document.createElement('div');
  banner.className = 'demo-banner';
  banner.textContent = 'УЧЕБНЫЙ ПРОЕКТ · Виртуальные показания · Изменения только в демо · Сообщения и подключения имитируются';
  document.querySelector('.navbar').after(banner);
  const panel = document.createElement('section');
  panel.className = 'demo-panel';
  panel.setAttribute('aria-label', 'Пошаговое демо');
  panel.innerHTML = `<div class="demo-panel__heading"><strong id="demo-title"></strong><label>Шаг <select id="demo-step" aria-label="Выбрать шаг демо"></select></label></div>
    <progress id="demo-progress" max="100" value="0" aria-label="Прогресс демо"></progress>
    <p id="demo-text" class="demo-panel__text"></p>
    <div class="demo-panel__actions"><button class="btn" id="demo-back">← Назад</button><button class="btn" id="demo-repeat">Повторить</button>
    <button class="btn" id="demo-example">Подставить пример</button><button class="btn" id="demo-alarm" hidden>Показать тревогу / норму</button><button class="btn" id="demo-open">Открыть шаг</button>
    <button class="btn btn-primary" id="demo-next">Далее →</button><button class="btn" id="demo-skip">Пропустить</button>
    <button class="btn" id="demo-exit">Выйти из демо</button><span class="demo-panel__status" id="demo-status" role="status" aria-live="polite"></span></div>`;
  document.body.append(panel);
  const intro = document.createElement('section');
  intro.id = 'demo-intro';
  intro.className = 'form-section demo-intro';
  const introTitle = document.createElement('h1');
  introTitle.textContent = 'Как пользоваться учебным путеводителем';
  intro.append(introTitle);
  (state.steps[0].intro || []).forEach(text => {
    const paragraph = document.createElement('p'); paragraph.textContent = text; intro.append(paragraph);
  });
  document.querySelector('.container').prepend(intro);
  const nudge = document.createElement('aside');
  nudge.className = 'demo-nudge'; nudge.hidden = true;
  nudge.innerHTML = '<span id="demo-nudge-text" role="status" aria-live="polite"></span><button class="btn" id="demo-reveal">Показать кнопку</button><button class="btn" id="demo-dismiss" aria-label="Закрыть подсказку">×</button>';
  const arrow = document.createElement('span');
  arrow.className = 'demo-nudge__arrow'; arrow.hidden = true; arrow.setAttribute('aria-hidden', 'true');
  document.body.append(nudge, arrow);
  const el = id => document.getElementById('demo-' + id);
  const currentPath = () => location.pathname.slice(boot.prefix.length).replace(/\/$/, '') || '/';
  const onStepPage = () => currentPath() === (state.steps[state.step].path.replace(/\/$/, '') || '/');
  function render() {
    const step = state.steps[state.step];
    const completed = state.completed.includes(state.step);
    el('title').textContent = `${state.step + 1}/${state.steps.length} · ${step.title}`;
    el('text').textContent = step.text;
    el('step').replaceChildren(...state.steps.map((s, index) => {
      const option = document.createElement('option');
      option.value = index;
      option.textContent = `${state.completed.includes(index) ? '✓ ' : state.skipped.includes(index) ? '↷ ' : ''}${index + 1}. ${s.title}`;
      // Visited steps form a contiguous sequence.
      const reached = Math.max(state.step, ...state.completed, ...state.skipped, 0);
      option.disabled = index > reached;
      return option;
    }));
    el('step').value = state.step;
    el('progress').value = Math.round((state.completed.length + state.skipped.length) / state.steps.length * 100);
    el('back').disabled = state.step === 0;
    el('next').disabled = Boolean(step.writes && !completed);
    el('next').textContent = state.step === state.steps.length - 1 ? 'Завершить ✓' : 'Далее →';
    el('skip').hidden = !step.writes || completed;
    el('example').disabled = !onStepPage() || !step.example;
    el('example').title = step.example ? 'Заполнить учебные значения; затем сохраните их на странице' : 'На этом шаге готовый пример не нужен';
    el('alarm').hidden = step.action !== 'alarm';
    el('alarm').disabled = !onStepPage();
    intro.hidden = state.step !== 0 || !onStepPage();
    el('open').hidden = onStepPage();
    el('status').textContent = `Выполнено ${state.completed.length}/${state.steps.length} · Пропущено ${state.skipped.length}` + (completed ? ' · Шаг выполнен' : '');
    document.querySelectorAll('.demo-focus').forEach(node => node.classList.remove('demo-focus'));
    if (onStepPage() && step.target) document.querySelector(step.target)?.classList.add('demo-focus');
    scheduleHint();
    if (!nudge.hidden) positionHint();
  }
  function hideHint() {
    clearTimeout(hintTimer);
    nudge.hidden = true; arrow.hidden = true; hintTarget = null;
  }
  function positionHint() {
    hintTarget = chooseHintTarget();
    if (!hintTarget || !hintTarget.isConnected) {hideHint(); return;}
    const rect = hintTarget.getBoundingClientRect();
    const panelTop = panel.getBoundingClientRect().top;
    const inPanel = panel.contains(hintTarget);
    const modal = hintTarget.closest('.modal');
    const modalRect = modal?.getBoundingClientRect();
    const bottom = inPanel ? innerHeight : Math.min(panelTop, modalRect?.bottom ?? innerHeight);
    const below = rect.bottom > bottom;
    const above = rect.top < Math.max(0, modalRect?.top ?? 0);
    const label = hintTarget.textContent.trim().replace(/\s+/g, ' ') || 'Продолжить';
    el('nudge-text').textContent = below ? `Кнопка «${label}» ниже. Прокрутите ${hintTarget.closest('.modal') ? 'окно настройки' : 'страницу'} вниз.`
      : above ? `Кнопка «${label}» выше. Прокрутите ${modal ? 'окно настройки' : 'страницу'} вверх.` : `Следующее действие: нажмите «${label}».`;
    arrow.textContent = above ? '↑' : '↓';
    const top = below ? Math.max(8, bottom - 66) : above ? 8 : Math.max(8, rect.top - 58);
    arrow.style.top = top + 'px';
    arrow.style.left = Math.max(8, Math.min(innerWidth - 56, rect.left + rect.width / 2 - 24)) + 'px';
  }
  function chooseHintTarget() {
    const controls = !onStepPage() ? ['#demo-open'] : state.completed.includes(state.step) ? ['#demo-next'] : state.steps[state.step].next_controls || ['#demo-next'];
    return controls.map(selector => document.querySelector(selector)).find(node => node && !node.disabled && node.getClientRects().length);
  }
  function scheduleHint() {
    const key = `${state.step}:${state.completed.includes(state.step)}:${onStepPage()}`;
    if (key === hintKey) return; // Five-second API refreshes must not restart the 20-second timer.
    hintKey = key; hideHint();
    if (state.step === state.steps.length - 1 && state.completed.includes(state.step)) return;
    hintTimer = setTimeout(() => {
      hintTarget = chooseHintTarget();
      if (!hintTarget) return;
      positionHint(); nudge.hidden = false;
      arrow.classList.remove('demo-nudge__arrow--blink');
      arrow.hidden = false; void arrow.offsetWidth;
      arrow.classList.add('demo-nudge__arrow--blink');
      setTimeout(() => {arrow.hidden = true;}, 2400);
    }, 20000);
  }
  el('reveal').onclick = () => {
    if (hintTarget) {hintTarget.scrollIntoView({behavior:'smooth', block:'center'}); hintTarget.focus({preventScroll:true});}
  };
  el('dismiss').onclick = hideHint;
  document.addEventListener('scroll', () => {if (!nudge.hidden) positionHint();}, true);
  window.addEventListener('resize', () => {if (!nudge.hidden) positionHint();});
  async function refresh() {
    const response = await rawFetch(window.kvtPath('/_demo/state'), {cache:'no-store'});
    if (!response.ok) throw new Error('Демо приостановлено. Запустите его снова.');
    state = await response.json(); render();
  }
  async function move(action, step) {
    if (busy) return;
    busy = true;
    try {
      const response = await rawFetch(window.kvtPath('/_demo/move'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, step})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      hideHint(); hintKey = null;
      state = result;
      if (action === 'next' && state.completed.length === state.steps.length) {
        render(); showToast('Демо пройдено. Можно повторить шаги или выйти.');
      } else location.assign(window.kvtPath(state.steps[state.step].path));
    } catch (error) { showToast(error.message, 'error'); }
    finally {busy = false;}
  }
  el('back').onclick = () => move('back');
  el('repeat').title = 'Восстановить настройки начала шага; сбросить результаты последующих шагов';
  el('repeat').onclick = () => move('repeat');
  el('next').onclick = () => move('next');
  el('skip').onclick = () => move('skip');
  el('step').onchange = event => move('goto', Number(event.target.value));
  el('open').onclick = () => location.assign(window.kvtPath(state.steps[state.step].path));
  el('example').onclick = async () => {
    const step = state.steps[state.step];
    if (step.action === 'sensor' && typeof window.openAddModal === 'function') window.openAddModal();
    Object.entries(step.example || {}).forEach(([id, value]) => {
      const field = document.getElementById(id);
      if (!field) return;
      if (typeof value === 'boolean') field.checked = value; else field.value = value;
      field.dispatchEvent(new Event('input', {bubbles:true}));
      field.dispatchEvent(new Event('change', {bubbles:true}));
    });
    showToast('Пример заполнен. Проверьте поля и нажмите «Сохранить» на странице.');
  };
  el('alarm').onclick = async () => {
    try { const result = await apiFetch('/_demo/alarm', {method:'POST'}); showToast(result.message); await refresh(); }
    catch (error) {showToast(error.message, 'error');}
  };
  el('exit').onclick = async () => {
    try {
      const response = await rawFetch('/demo/end', {method:'POST'});
      if (!response.ok) throw new Error('Не удалось завершить демо');
      location.assign('/');
    } catch (error) {showToast(error.message, 'error');}
  };
  new ResizeObserver(() => {
    const height = panel.getBoundingClientRect().height + 24;
    document.body.style.paddingBottom = height + 'px';
    document.body.style.setProperty('--demo-panel-height', height + 'px');
    if (!nudge.hidden) positionHint();
  }).observe(panel);
  render();
  if (new URLSearchParams(location.search).has('demo_resume')) location.replace(window.kvtPath(state.steps[state.step].path));
  // Page scripts can build editors asynchronously after this common script.
  window.addEventListener('load', () => render());
  setInterval(() => refresh().catch(error => {el('status').textContent = error.message;}), 5000);
})();
