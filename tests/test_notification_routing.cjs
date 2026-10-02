// Browser integration for the recipient editor. No external network requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.KVT_PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    await page.setContent('<form id="form"><div id="root"></div></form>');
    await page.addScriptTag({path: path.resolve(__dirname, '../visualizer/static/js/notification_routing.js')});
    await page.evaluate(() => {
      window.editor = new NotificationRouting(document.getElementById('root'),
        [{id: 1, name: 'Склад <script>bad</script>'}, {id: 2, name: 'Кабинет'}],
        {'1': {email_on_alarm: false, email_recipients: ['personal@example.test']}},
        [{id: 'group-a', name: 'Группа A', sensor_ids: [1, 2], telegram_chat_ids: ['-100123']}]);
    });
    assert.equal(await page.locator('#root script').count(), 0);
    assert.deepEqual(await page.evaluate(() => window.editor.payload()), {
      per_sensor: {'1': {email_on_alarm: false, email_recipients: ['personal@example.test']}},
      groups: [{id: 'group-a', name: 'Группа A', sensor_ids: [1, 2], telegram_chat_ids: ['-100123']}]
    });
    const row = page.locator('tbody tr').first();
    await row.locator('select').first().selectOption('inherit');
    assert.deepEqual((await page.evaluate(() => window.editor.payload())).per_sensor['1'], {email_on_alarm: false});
    await row.locator('select').first().selectOption('off');
    assert.deepEqual((await page.evaluate(() => window.editor.payload())).per_sensor['1'].email_recipients, []);
    await row.locator('select').first().selectOption('custom');
    await row.locator('input').first().fill('new@example.test, shared@example.test, new@example.test');
    assert.deepEqual((await page.evaluate(() => window.editor.payload())).per_sensor['1'].email_recipients,
                     ['new@example.test', 'shared@example.test']);
    await page.getByRole('button', {name: '+ Добавить группу'}).click();
    const added = page.locator('#root > div').first().locator('.form-section').last();
    await added.locator('input').first().fill('Новая группа');
    await added.locator('summary').click();
    await added.locator('input[type=checkbox]').first().check();
    await added.locator('select').first().selectOption('custom');
    await added.getByRole('textbox', {name: 'Почта группы: получатели через запятую'}).fill('group@example.test');
    const payload = await page.evaluate(() => window.editor.payload());
    assert.equal(payload.groups.length, 2);
    assert.equal(payload.groups[1].name, 'Новая группа');
    assert.deepEqual(payload.groups[1].sensor_ids, [1]);
    assert.deepEqual(payload.groups[1].email_recipients, ['group@example.test']);
    await added.getByRole('button', {name: 'Удалить группу'}).click();
    assert.equal((await page.evaluate(() => window.editor.payload())).groups.length, 1);
    await page.getByRole('searchbox').fill('Кабинет');
    assert.equal(await page.locator('tbody tr:visible').count(), 1);
    // Inline code is parsed separately because its sensor/config data comes from Jinja.
    const source = fs.readFileSync(path.resolve(__dirname, '../visualizer/templates/settings/notifications.html'), 'utf8');
    const script = source.split('<script>')[1].split('</script>')[0]
      .replace(/\{\{[\s\S]*?\}\}/g, '[]');
    new Function(script);
    console.log('Recipient editor browser checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
