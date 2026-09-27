const {test,expect}=require('@playwright/test');
test.use({serviceWorkers:'allow'});

test('installed shell reloads offline without cached advice',async({page,context})=>{
  await page.route('**/api/conditions?**',r=>r.fulfill({status:503,json:{detail:'Offline-shell test: forecast intentionally unavailable'}}));
  await page.goto('/');
  await page.evaluate(()=>navigator.serviceWorker.ready);
  await context.setOffline(true);
  await page.reload({waitUntil:'domcontentloaded'});
  await expect(page.locator('#connection')).toContainText(/Offline|unavailable/);
  await expect(page.locator('#plan-route')).toBeDisabled();
  await expect(page.locator('#wave-value')).toHaveText('—');
  const paths=await page.evaluate(async()=>{
    const cache=await caches.open('kadal-shell-v5');
    return (await cache.keys()).map(request=>new URL(request.url).pathname);
  });
  expect(paths).toContain('/app.js');
  expect(paths.some(path=>path.startsWith('/api/'))).toBeFalsy();
});
