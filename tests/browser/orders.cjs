const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../..');
const base = process.env.CAD_TEST_BASE_URL || 'http://127.0.0.1:8000';
const python = process.env.CAD_TEST_PYTHON || path.join(root, '.venv/bin/python');
const shell = code => execFileSync(python, ['manage.py', 'shell', '-c', code], {cwd: root, encoding: 'utf8'}).trim().split('\n').at(-1);
const suffix = randomUUID();
const email = `browser-${suffix}@example.test`;
const adminEmail = `admin-${suffix}@example.test`;
const designerEmail = `designer-${suffix}@example.test`;
const password = 'Browser-test-829!';
const stl = Buffer.alloc(134); stl.write('binary STL'); stl.writeUInt32LE(1, 80);
const orderIds = [];

async function login(page, address, next = '/orders/') {
  await page.goto(`${base}/accounts/login/?next=${encodeURIComponent(next)}`);
  await page.locator('#id_username').fill(address);
  await page.locator('#id_password').fill(password);
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await page.waitForURL(base + next);
}

async function drop(page, name, filename = 'working.stl') {
  await page.locator(`input[name="${name}"]`).evaluate((input, payload) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(payload.bytes)], payload.name));
    input.closest('[data-drop-zone]').dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: transfer}));
  }, {name: filename, bytes: [...stl]});
}

async function start(page, kind) {
  await page.goto(base + '/orders/new/');
  await page.locator(`[name=service][data-kind=${kind}]`).check();
  await page.locator('#id_material').selectOption('zirconia');
}

async function tooth(page, name, value) {
  await page.locator(`[name=${name}][value="${value}"]`).check();
}

async function submit(page) {
  await drop(page, 'working');
  await page.locator('#id_review_order').check();
  await page.getByRole('button', {name: 'Отправить заказ', exact: true}).click();
  await page.waitForURL(/\/orders\/\d+\/$/);
  const id = Number(page.url().match(/orders\/(\d+)\//)[1]);
  orderIds.push(id);
  return id;
}

async function saveAdmin(page) {
  await Promise.all([page.waitForNavigation(), page.locator('[name=_continue]').click()]);
  assert.equal(await page.locator('.errorlist').count(), 0, await page.locator('body').innerText());
  assert.ok(await page.locator('.messagelist .success').count());
}

async function checkWidth(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
}

(async () => {
  const fixture = JSON.parse(shell(`
import base64,json
from django.conf import settings
assert settings.DEBUG, 'Browser fixtures are restricted to a development database'
from accounts.models import User
from orders.models import Service,Tariff
from orders.tests import picture
u=User.objects.create_user('${email}','${password}',first_name='Тестовый врач',email_verified=True)
u.company.free_crown_units=3; u.company.save()
a=User.objects.create_superuser('${adminEmail}','${password}',first_name='Тестовый администратор')
d=User.objects.create_user('${designerEmail}','${password}',first_name='Тестовый дизайнер',role='designer',is_staff=True,email_verified=True)
for kind,rate in [('crown',500),('bridge',450),('inlay',600),('implant',900)]:
    Tariff.objects.create(organization=u.company,service=Service.objects.get(kind=kind),unit_price=rate)
print(json.dumps({'designer':d.pk,'company':u.company_id,'png':base64.b64encode(picture().read()).decode()}))
`));
  const browser = await chromium.launch({headless: true, executablePath: process.env.CAD_BROWSER_BIN || '/usr/bin/chromium', args: ['--no-sandbox']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  const failures = [];
  context.on('page', p => p.on('pageerror', error => failures.push(error.message)));
  page.on('pageerror', error => failures.push(error.message));
  try {
    await login(page, email);
    await start(page, 'crown');
    assert.equal(await page.locator('#id_implant_system').isVisible(), false);
    assert.equal(await page.locator('#id_implant_type').isVisible(), false);
    assert.equal(await page.locator('[name=units]').count(), 0);
    await tooth(page, 'selected_teeth', '16');
    await tooth(page, 'selected_teeth', '17');
    await page.waitForFunction(() => document.querySelector('[data-summary=price]').textContent.includes('0,00'));
    await drop(page, 'working');
    await drop(page, 'antagonist', 'antagonist.stl');
    await page.locator('#id_review_order').check();
    await page.evaluate(() => {
      window.savedInput = document.querySelector('#id_working');
      const invalid = document.createElement('input');
      Object.assign(invalid, {type: 'checkbox', name: 'selected_teeth', value: '99', checked: true});
      document.querySelector('[data-order-builder]').appendChild(invalid);
    });
    await page.getByRole('button', {name: 'Отправить заказ', exact: true}).click();
    await page.locator('.errorlist').first().waitFor();
    assert.equal(await page.evaluate(() => document.querySelector('#id_working') === window.savedInput), true);
    assert.equal(await page.locator('#id_working').evaluate(input => input.files[0].name), 'working.stl');
    await page.setViewportSize({width: 390, height: 844});
    await checkWidth(page);
    await page.screenshot({path: '/tmp/cad-builder-mobile.png', fullPage: true});
    await page.getByRole('button', {name: 'Отправить заказ', exact: true}).click();
    await page.waitForURL(/\/orders\/\d+\/$/);
    const crown = Number(page.url().match(/orders\/(\d+)\//)[1]); orderIds.push(crown);

    await page.setViewportSize({width: 1440, height: 1000});
    await start(page, 'bridge');
    await tooth(page, 'support_teeth', '35');
    await tooth(page, 'support_teeth', '37');
    await tooth(page, 'pontic_teeth', '36');
    assert.equal(await page.locator('[name=support_teeth][value="36"]').isDisabled(), true);
    await page.waitForFunction(() => document.querySelector('[data-summary=price]').textContent.includes('350,00'));
    await page.screenshot({path: '/tmp/cad-builder-desktop.png', fullPage: true});
    const bridge = await submit(page);

    await start(page, 'inlay');
    await tooth(page, 'selected_teeth', '26');
    await page.locator('#id_inlay_type').selectOption('overlay');
    assert.equal(await page.locator('#id_implant_system').isVisible(), false);
    await submit(page);

    await start(page, 'implant');
    await tooth(page, 'selected_teeth', '46');
    await page.locator('#id_implant_type').selectOption('ready_abutment');
    assert.equal(await page.locator('#id_scanbody').isVisible(), false);
    await submit(page);

    await start(page, 'implant');
    await tooth(page, 'selected_teeth', '36');
    await page.locator('#id_implant_type').selectOption('ti_base');
    for (const name of ['implant_system','implant_platform','scanbody','abutment']) {
      await page.locator(`#id_${name}_unknown`).check();
    }
    await page.locator('#id_scanbody_on_working').check();
    assert.equal(await page.locator('#id_scanbody_file').isVisible(), false);
    const implant = await submit(page);
    await page.getByText('Требуется уточнение', {exact: true}).first().waitFor();

    const adminContext = await browser.newContext();
    const adminPage = await adminContext.newPage();
    await login(adminPage, adminEmail, '/admin/');
    await adminPage.goto(`${base}/admin/orders/order/${crown}/change/`);
    await adminPage.locator('#id_designer').selectOption(String(fixture.designer));
    await adminPage.locator('#id_next_status').selectOption('in_progress');
    await saveAdmin(adminPage);
    await adminPage.locator('#id_result_upload').setInputFiles({name: 'result-v1.stl', mimeType: 'application/octet-stream', buffer: stl});
    await saveAdmin(adminPage);
    const resultId = await adminPage.locator('#id_publish_file option').last().getAttribute('value');
    await adminPage.locator('#id_publish_file').selectOption(resultId);
    await adminPage.locator('#id_quality_confirmed').check();
    await adminPage.locator('#id_next_status').selectOption('ready');
    await saveAdmin(adminPage);
    await page.goto(`${base}/orders/${crown}/`);
    await page.getByRole('link', {name: 'Скачать result-v1.stl', exact: true}).waitFor();
    await page.locator('#id_revision_text').fill('Исправить контакт по изображению.');
    await page.locator('#id_file').setInputFiles({name: 'correction.png', mimeType: 'image/png', buffer: Buffer.from(fixture.png, 'base64')});
    await page.getByRole('button', {name: 'Запросить исправление', exact: true}).click();
    await page.getByText('Исправление запрошено.', {exact: true}).waitFor();
    await page.getByText('На доработке', {exact: true}).first().waitFor();
    assert.ok((await page.locator('[data-files=source]').innerText()).includes('correction.png'));
    assert.ok((await page.locator('[data-files=result]').innerText()).includes('result-v1.stl'));

    await adminPage.goto(`${base}/admin/orders/order/${bridge}/change/`);
    await adminPage.locator('#id_new_price').fill('1500');
    await adminPage.locator('#id_reason').fill('Дополнительная работа');
    await saveAdmin(adminPage);
    await page.goto(`${base}/orders/${bridge}/`);
    await page.getByRole('button', {name: 'Подтвердить стоимость', exact: true}).click();
    await page.getByText('Стоимость подтверждена. Команда может принять заказ в работу.', {exact: true}).waitFor();
    const state = JSON.parse(shell(`from orders.models import Order; import json; a=Order.objects.get(pk=${bridge}); b=Order.objects.get(pk=${implant}); print(json.dumps({'price':str(a.effective_price),'confirmed':bool(a.price_accepted_at),'implant':b.status,'deadline':bool(b.deadline)}))`));
    assert.equal(state.price, '1500.00'); assert.equal(state.confirmed, true);
    assert.equal(state.implant, 'needs_data'); assert.equal(state.deadline, false);

    await adminPage.goto(`${base}/admin/orders/order/${implant}/change/`);
    await adminPage.locator('#id_designer').selectOption(String(fixture.designer));
    await adminPage.locator('#id_clarification_resolved').check();
    await adminPage.locator('#id_next_status').selectOption('in_progress');
    await Promise.all([adminPage.waitForNavigation(), adminPage.locator('[name=_continue]').click()]);
    assert.ok(await adminPage.locator('.errorlist').count());
    for (const name of ['implant_system','implant_platform','scanbody','abutment']) {
      await adminPage.locator(`#id_${name}`).fill('Уточнённый параметр');
    }
    await saveAdmin(adminPage);
    assert.equal(shell(`from orders.models import Order; print(Order.objects.get(pk=${implant}).status)`), 'in_progress');

    for (const width of [390, 320]) {
      await page.setViewportSize({width, height: 844});
      await page.goto(base + '/orders/');
      assert.equal(await page.locator('.order-list-desktop').isVisible(), false);
      assert.equal(await page.locator('.mobile-order').count(), 5);
      await checkWidth(page);
      await page.screenshot({path: `/tmp/cad-orders-${width}.png`, fullPage: true});
    }
    assert.deepEqual(failures, []);
    console.log('PASS: four constructions, dynamic fields, tooth roles, server quotes, retained files, five submissions, QA, image correction, price approval, mobile cards at 390/320px.');
  } finally {
    await browser.close();
    shell(`from accounts.models import User,Organization; from orders.models import OrderAccess,private_storage; u=User.objects.get(email='${email}'); [private_storage.delete(f) for f in u.company.orders.values_list('files__file',flat=True) if f]; u.company.orders.all().delete(); people=User.objects.filter(email__in=['${email}','${adminEmail}','${designerEmail}']); OrderAccess.objects.filter(actor__in=people).delete(); people.delete(); Organization.objects.filter(pk=${fixture.company}).delete(); print('cleaned')`);
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
