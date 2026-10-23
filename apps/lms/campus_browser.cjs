const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH);

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const origin = process.env.CAMPUS_TEST_URL;
    const course = `${origin}/lms/campus/courses/${process.env.CAMPUS_TEST_COURSE_ID}/`;
    await page.goto(`${origin}/lms/campus/`);
    assert.match(await page.locator('h1').innerText(), /Mis cursos/);
    await page.goto(course);
    assert.match(page.url(), /accounts\/login/);
    await context.addCookies([{
      name: process.env.CAMPUS_TEST_COOKIE_NAME,
      value: process.env.CAMPUS_TEST_SESSION,
      url: origin,
    }]);
    await page.goto(course);
    await page.getByRole('button', { name: 'Inscribirme', exact: true }).click();
    await page.getByRole('link', { name: /Continuar:/ }).click();
    assert.match(await page.locator('body').innerText(), /PRIVATE_LESSON_BODY/);
    await page.goto(course);
    assert.match(await page.getByRole('link', { name: /Continuar:/ }).innerText(), /Lecci/);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: /Marcar como completado/ }).click();
    await page.goto(course);
    assert.equal(await page.locator('progress').getAttribute('value'), '100');
    assert.match(await page.locator('body').innerText(), /Curso superado: S/);
    console.log('PASS: visitor gate, isolated authenticated enrollment, lesson, resume and mobile completion');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
