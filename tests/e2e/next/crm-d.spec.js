// Stream D: وظایف / یادآورها / پیامک / ارزیابی روزانه (DPA) / گزارش.
const { test, expect } = require('@playwright/test');
const { signIn, watchProblems, noHorizontalScroll, scrollThrough, a11y } = require('./helpers');

const stamp = Date.now();


test.describe('tasks', () => {
  test('loads with data, creates, edits, completes and deletes a task', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/tasks');
    await expect(page.getByRole('heading', { name: 'وظایف' })).toBeVisible();

    const title = `e2e-d-task-${stamp}`;
    await page.getByRole('button', { name: 'وظیفهٔ تازه' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('عنوان').fill(title);
    await dialog.getByLabel('اولویت').selectOption('urgent');
    await dialog.getByRole('button', { name: 'ثبت وظیفه' }).click();
    await expect(page.getByText('وظیفه ثبت شد').first()).toBeVisible();

    const row = page.getByRole('row', { name: new RegExp(title) });
    await expect(row).toBeVisible();
    await expect(row.getByText('فوری')).toBeVisible();

    // edit
    await row.getByRole('button', { name: 'ویرایش وظیفه' }).click();
    const editDialog = page.getByRole('dialog');
    await editDialog.getByLabel('اولویت').selectOption('low');
    await editDialog.getByRole('button', { name: 'ذخیرهٔ تغییرات' }).click();
    await expect(page.getByText('وظیفه ذخیره شد').first()).toBeVisible();
    await expect(row.getByText('کم')).toBeVisible();

    // quick done via checkbox
    await row.getByRole('checkbox').click();
    await expect(row.getByText('انجام شده')).toBeVisible();

    // status filter narrows the list
    await page.getByLabel('وضعیت').selectOption('todo');
    await expect(row).toHaveCount(0);
    await page.getByLabel('وضعیت').selectOption('');

    // board view shows the same task
    await page.getByRole('button', { name: 'تابلو' }).click();
    await expect(page.getByText(title)).toBeVisible();
    await page.getByRole('button', { name: 'فهرست' }).click();

    // delete
    await row.getByRole('button', { name: 'حذف وظیفه' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'حذف' }).click();
    await expect(page.getByText('وظیفه حذف شد').first()).toBeVisible();
    await expect(page.getByRole('row', { name: new RegExp(title) })).toHaveCount(0);

    await noHorizontalScroll(page);
    expect((await a11y(page))).toEqual([]);
    expect(problems).toEqual([]);
  });

  test('an overdue task is visibly marked', async ({ page }) => {
    const session = await signIn(page, 'owner');
    const title = `e2e-d-overdue-${stamp}`;
    await page.request.post('/api/crm/tasks', {
      data: { title, priority: 'high', status: 'todo', due_date: new Date(Date.now() - 3600_000).toISOString() },
      headers: { 'X-CSRF-Token': session.csrf_token },
    });
    await page.goto('/panel/crm/tasks');
    const row = page.getByRole('row', { name: new RegExp(title) });
    await expect(row).toHaveClass(/destructive/);
  });
});

test.describe('reminders', () => {
  test('loads with data, creates an SMS reminder, edits and deletes it', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/reminders');
    await expect(page.getByRole('heading', { name: 'یادآورها' })).toBeVisible();

    const title = `e2e-d-reminder-${stamp}`;
    await page.getByRole('button', { name: 'یادآور تازه' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('عنوان').fill(title);
    await dialog.getByLabel('زمان یادآوری').fill('1405/08/01 10:00');
    await dialog.getByLabel('کانال').selectOption('sms');
    await expect(dialog.getByLabel('شمارهٔ گیرنده')).toBeVisible();
    await dialog.getByLabel('شمارهٔ گیرنده').fill('09121234567');
    await dialog.getByRole('button', { name: 'ثبت یادآور' }).click();
    await expect(page.getByText('یادآور ثبت شد').first()).toBeVisible();

    const row = page.getByRole('row', { name: new RegExp(title) });
    await expect(row).toBeVisible();
    await expect(row.getByText('پیامک')).toBeVisible();

    // edit through the new PATCH endpoint
    await row.getByRole('button', { name: 'ویرایش یادآور' }).click();
    const editDialog = page.getByRole('dialog');
    await editDialog.getByLabel('کانال').selectOption('in_app');
    await editDialog.getByRole('button', { name: 'ذخیرهٔ تغییرات' }).click();
    await expect(page.getByText('یادآور ذخیره شد').first()).toBeVisible();
    await expect(row.getByText('در برنامه')).toBeVisible();

    // sent filter
    await page.getByLabel('وضعیت').selectOption('sent');
    await expect(row).toHaveCount(0);
    await page.getByLabel('وضعیت').selectOption('');
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'حذف یادآور' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'حذف' }).click();
    await expect(page.getByText('یادآور حذف شد').first()).toBeVisible();

    await noHorizontalScroll(page);
    expect((await a11y(page))).toEqual([]);
    expect(problems).toEqual([]);
  });
});

test.describe('sms', () => {
  test('shows a live character/segment count and the send log', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/sms');
    await expect(page.getByRole('heading', { name: 'پیامک', exact: true })).toBeVisible();

    const message = page.getByLabel('متن پیامک');
    await message.fill('سلام');
    await expect(page.getByText(/۴ کاراکتر — ۱ بخش \(۷۰ کاراکتر در هر بخش\)/)).toBeVisible();

    // 71 chars forces a second, 67-char part
    await message.fill('ا'.repeat(71));
    await expect(page.getByText(/۷۱ کاراکتر — ۲ بخش \(۶۷ کاراکتر در هر بخش\)/)).toBeVisible();

    await expect(page.getByRole('heading', { name: 'تاریخچهٔ ارسال' })).toBeVisible();

    await noHorizontalScroll(page);
    expect((await a11y(page))).toEqual([]);
    expect(problems).toEqual([]);
  });
});

test.describe('dpa', () => {
  test('the score is computed live and matches the saved record, then edits and deletes', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/dpa');
    await expect(page.getByRole('heading', { name: 'ارزیابی روزانه' })).toBeVisible();

    const agent = `e2e-d-agent-${stamp}`;
    await page.getByRole('button', { name: 'ارزیابی تازه' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('نام مشاور').fill(agent);
    // base tasks: prospecting(25) + showings(30) = 55
    await dialog.getByRole('checkbox', { name: /شکار فایل/ }).check();
    await dialog.getByRole('checkbox', { name: /بازدید حضوری/ }).check();
    // bonus: 1 exclusive = +30; penalty: 1 crm delay = -10
    await dialog.locator('label', { hasText: 'ثبت فایل انحصاری' }).getByRole('spinbutton').fill('1');
    await dialog.locator('label', { hasText: 'تأخیر در ثبت CRM' }).getByRole('spinbutton').fill('1');
    // total = 55 base + 0 activity + 30 bonus - 10 penalty = 75
    await expect(dialog.getByText('۷۵', { exact: true })).toBeVisible();

    await dialog.getByRole('button', { name: 'ذخیرهٔ فرم' }).click();
    await expect(page.getByText('فرم ارزیابی ذخیره شد').first()).toBeVisible();

    const row = page.getByRole('row', { name: new RegExp(agent) });
    await expect(row).toBeVisible();
    await expect(row.getByText('۷۵')).toBeVisible();

    // search filter
    await page.getByLabel('جستجوی مشاور').fill(agent);
    await expect(row).toBeVisible();
    await page.getByLabel('جستجوی مشاور').fill('no-such-agent-xyz');
    await expect(row).toHaveCount(0);
    await page.getByLabel('جستجوی مشاور').fill('');

    // edit
    await row.getByRole('button', { name: 'ویرایش ارزیابی' }).click();
    const editDialog = page.getByRole('dialog');
    await editDialog.getByRole('checkbox', { name: /حلقه پیگیری/ }).check();
    await editDialog.getByRole('button', { name: 'ذخیرهٔ فرم' }).click();
    await expect(page.getByText('فرم ارزیابی ذخیره شد').first()).toBeVisible();
    await expect(row.getByText('۹۵')).toBeVisible(); // +20 followup

    await row.getByRole('button', { name: 'حذف ارزیابی' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'حذف' }).click();
    await expect(page.getByText('رکورد حذف شد').first()).toBeVisible();

    await noHorizontalScroll(page);
    expect((await a11y(page))).toEqual([]);
    expect(problems).toEqual([]);
  });
});

test.describe('report', () => {
  test('stat cards, funnel and charts render from /crm/stats', async ({ page }) => {
    const problems = watchProblems(page);
    await signIn(page, 'owner');
    await page.goto('/panel/crm/report');
    await expect(page.getByRole('heading', { name: 'گزارش' })).toBeVisible();
    // «وظایف» and «معاملات» also name nav tabs, so the stat cards are
    // checked by the labels that are unique to them.
    for (const label of ['کل لیدها', 'مخاطبان']) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(page.getByText('قیف لیدها')).toBeVisible();
    await scrollThrough(page);
    await expect(page.getByText('مخاطبان به تفکیک نوع')).toBeVisible();

    await noHorizontalScroll(page);
    expect((await a11y(page))).toEqual([]);
    expect(problems).toEqual([]);
  });

  test('the dark theme passes the same accessibility check', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await signIn(page, 'owner');
    await page.goto('/panel/crm/report');
    await expect(page.locator('html')).toHaveClass(/dark/);
    await scrollThrough(page);
    expect((await a11y(page))).toEqual([]);
  });
});
