/* Recipient overrides: a missing key inherits; an empty list disables delivery. */
class NotificationRouting {
  constructor(root, sensors, perSensor, groups) {
    this.root = root;
    this.original = perSensor;
    this.sensorRows = new Map();
    this.groupRows = [];
    this.sensors = sensors.map(s => ({id: Number(s.id), name: s.name || String(s.id)}));
    const ids = new Set(this.sensors.map(s => s.id));
    const referenced = [...Object.keys(perSensor).map(Number), ...groups.flatMap(g => g.sensor_ids)];
    for (const id of referenced) {
      if (!ids.has(id)) {
        this.sensors.push({id, name: 'Датчик удалён из системы'});
        ids.add(id);
      }
    }
    const heading = this.element('h3', 'Группы уведомлений');
    root.append(heading);
    this.groupsContainer = this.element('div');
    root.append(this.groupsContainer);
    for (const group of groups) this.addGroup(group);
    const add = this.element('button', '+ Добавить группу');
    add.type = 'button';
    add.className = 'btn btn-secondary';
    add.addEventListener('click', () => this.addGroup());
    root.append(add, this.element('h3', 'Отдельные датчики'));
    const filterLabel = this.element('label', 'Найти датчик');
    const filter = this.element('input');
    filter.type = 'search';
    filterLabel.append(filter);
    root.append(filterLabel);
    const wrapper = this.element('div');
    wrapper.className = 'table-wrap';
    const table = this.element('table');
    const head = this.element('thead');
    const headers = this.element('tr');
    for (const text of ['Датчик', 'Почта', 'Telegram']) headers.append(this.element('th', text));
    head.append(headers);
    table.append(head);
    const body = this.element('tbody');
    for (const sensor of this.sensors) {
      const row = this.element('tr');
      const label = sensor.id + ' — ' + sensor.name;
      row.dataset.search = label.toLowerCase();
      row.append(this.element('td', label));
      const settings = perSensor[String(sensor.id)] || {};
      const controls = {};
      for (const [key, channel] of [['email_recipients', 'Почта'], ['telegram_chat_ids', 'Telegram']]) {
        const cell = this.element('td');
        controls[key] = this.targetControl(cell, settings, key, channel + ': ' + label);
        row.append(cell);
      }
      this.sensorRows.set(String(sensor.id), controls);
      body.append(row);
    }
    table.append(body);
    wrapper.append(table);
    root.append(wrapper);
    filter.addEventListener('input', () => {
      const text = filter.value.trim().toLowerCase();
      for (const row of body.children) row.hidden = !row.dataset.search.includes(text);
    });
  }

  element(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  targetControl(parent, settings, key, label) {
    const select = this.element('select');
    select.setAttribute('aria-label', label + ': режим');
    for (const [mode, title] of [['inherit', 'Наследовать'], ['custom', 'Свои адреса'], ['off', 'Не отправлять']]) {
      const option = this.element('option', title);
      option.value = mode;
      select.append(option);
    }
    const present = Object.hasOwn(settings, key);
    select.value = present ? (settings[key].length ? 'custom' : 'off') : 'inherit';
    const input = this.element('input');
    input.type = 'text';
    input.setAttribute('aria-label', label + ': получатели через запятую');
    input.placeholder = key === 'email_recipients' ? 'user@example.ru, other@example.ru' : '-100123, 123456';
    input.value = (settings[key] || []).join(', ');
    const update = () => {
      input.disabled = select.value !== 'custom';
      input.required = select.value === 'custom';
      input.hidden = input.disabled;
    };
    select.addEventListener('change', update);
    update();
    parent.append(select, input);
    return {select, input};
  }

  addGroup(group) {
    if (!group) {
      let id;
      do { id = 'group-' + Math.random().toString(36).slice(2, 12); }
      while (this.groupRows.some(row => row.id === id));
      group = {id, name: '', sensor_ids: []};
    }
    const card = this.element('div');
    card.className = 'form-section';
    const nameLabel = this.element('label', 'Имя группы');
    const name = this.element('input');
    name.required = true;
    name.maxLength = 120;
    name.value = group.name;
    name.placeholder = 'Например, склад № 1';
    nameLabel.append(name);
    const remove = this.element('button', 'Удалить группу');
    remove.type = 'button';
    remove.className = 'btn btn-sm btn-danger';
    card.append(nameLabel, remove);
    const members = this.element('details');
    const summary = this.element('summary');
    members.append(summary);
    const choices = this.element('div');
    choices.style.maxHeight = '220px';
    choices.style.overflowY = 'auto';
    const selected = new Set(group.sensor_ids);
    const checkboxes = [];
    const updateCount = () => summary.textContent = 'Выбрать датчики (' + checkboxes.filter(c => c.checked).length + ')';
    for (const sensor of this.sensors) {
      const label = this.element('label');
      label.style.display = 'block';
      const checkbox = this.element('input');
      checkbox.type = 'checkbox';
      checkbox.value = String(sensor.id);
      checkbox.checked = selected.has(sensor.id);
      checkbox.addEventListener('change', updateCount);
      checkboxes.push(checkbox);
      label.append(checkbox, document.createTextNode(' ' + sensor.id + ' — ' + sensor.name));
      choices.append(label);
    }
    updateCount();
    members.append(choices);
    card.append(members);
    const controls = {};
    for (const [key, title] of [['email_recipients', 'Почта группы'], ['telegram_chat_ids', 'Telegram группы']]) {
      const section = this.element('div');
      section.className = 'form-group';
      section.append(this.element('label', title));
      controls[key] = this.targetControl(section, group, key, title);
      card.append(section);
    }
    const record = {id: group.id, name, checkboxes, controls};
    this.groupRows.push(record);
    this.groupsContainer.append(card);
    remove.addEventListener('click', () => {
      this.groupRows = this.groupRows.filter(row => row !== record);
      card.remove();
    });
  }

  applyTargets(settings, controls) {
    for (const [key, {select, input}] of Object.entries(controls)) {
      delete settings[key];
      if (select.value === 'off') settings[key] = [];
      if (select.value === 'custom') settings[key] = [...new Set(input.value.split(',').map(s => s.trim()).filter(Boolean))];
    }
    return settings;
  }

  payload() {
    const per_sensor = {};
    for (const [id, controls] of this.sensorRows) {
      const settings = this.applyTargets({...this.original[id]}, controls);
      if (Object.keys(settings).length) per_sensor[id] = settings;
    }
    const groups = this.groupRows.map(row => this.applyTargets({
      id: row.id, name: row.name.value.trim(),
      sensor_ids: row.checkboxes.filter(c => c.checked).map(c => Number(c.value))
    }, row.controls));
    return {per_sensor, groups};
  }
}
