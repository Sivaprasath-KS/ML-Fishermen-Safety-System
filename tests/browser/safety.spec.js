const {test, expect} = require('@playwright/test');
const fixture = (model = 'auto') => ({
  current:{time:'2026-09-27T06:00:00+00:00',wave_m:1.24,wind_ms:7.8},
  current_risk:{label:'safe'},
  hourly:[{time:'2026-09-27T11:00:00Z',wind_ms:99},{time:'2026-09-27T12:00:00Z',wind_ms:8.3}],
  prediction:{available:true,wave_m:model==='xgboost'?1.52:1.48,target_time:'2026-09-27T12:00:00+00:00',risk_prediction:{available:true,label:model==='xgboost'?'danger':'moderate'}}
});
test.beforeEach(async({page})=>{
  await page.route('**/api/conditions?**',r=>r.fulfill({json:fixture(new URL(r.request().url()).searchParams.get('model'))}));
});
test('manual coordinates and every model use existing API and actual response fields',async({page})=>{
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('Failed to load resource'))errors.push(m.text());});
  await page.goto('/');
  await expect(page.locator('#prediction-model')).toHaveValue('auto');
  await expect(page.locator('#wave-value')).toHaveText('1.24');
  await expect(page.locator('#wind-value')).toHaveText('7.8');
  await expect(page.locator('#prediction-wind-value')).toHaveText('8.3');
  for(const model of ['auto','random_forest','xgboost']){
    await page.locator('#safety-lat').fill('12.5');
    await page.locator('#safety-lon').fill('80.5');
    await page.selectOption('#prediction-model',model);
    const request=page.waitForRequest(r=>r.url().includes('/api/conditions?'));
    await page.locator('#predict-safety').click();
    const url=new URL((await request).url());
    expect(url.searchParams.get('model')).toBe(model);
    expect(url.searchParams.get('latitude')).toBe('12.5');
    expect(url.searchParams.get('longitude')).toBe('80.5');
    await expect(page.locator('#prediction-value')).toHaveText(model==='xgboost'?'1.52':'1.48');
    await expect(page.locator('#forecast-risk')).toHaveText(model==='xgboost'?'Danger':'Moderate');
  }
  expect(errors).toEqual([]);
});
test('GPS success, denied, unavailable and outside coverage',async({page})=>{
  await page.goto('/');
  await expect(page.locator('#wave-value')).toHaveText('1.24');
  await page.evaluate(()=>navigator.geolocation.getCurrentPosition=ok=>ok({coords:{latitude:12.55,longitude:80.44}}));
  await page.locator('#safety-gps').click();
  await expect(page.locator('#safety-lat')).toHaveValue('12.55000');
  await expect(page.locator('#safety-lon')).toHaveValue('80.44000');
  await expect(page.locator('#forecast-risk')).toHaveText('Unavailable');
  await page.locator('#predict-safety').click();
  await expect(page.locator('#forecast-risk')).toHaveText('Moderate');
  await page.evaluate(()=>navigator.geolocation.getCurrentPosition=(_,fail)=>fail({code:1}));
  await page.locator('#safety-gps').click();
  await expect(page.locator('#safety-input-status')).toContainText('Location unavailable');
  await page.evaluate(()=>navigator.geolocation.getCurrentPosition=ok=>ok({coords:{latitude:20,longitude:80}}));
  await page.locator('#safety-gps').click();
  await expect(page.locator('#safety-input-status')).toContainText('outside');
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{value:undefined,configurable:true}));
  await page.locator('#safety-gps').click();
  await expect(page.locator('#safety-input-status')).toHaveText('GPS is unavailable.');
});
test('loading, stale response, invalid input, API error and missing wind',async({page})=>{
  await page.goto('/');
  await expect(page.locator('#wave-value')).toHaveText('1.24');
  await page.locator('#safety-lat').fill('30');
  expect(await page.locator('#safety-lat').evaluate(el=>el.validity.valid)).toBe(false);
  await page.locator('#predict-safety').click();
  await expect(page.locator('#wave-value')).toHaveText('—');
  await page.locator('#safety-lat').fill('12.5');
  let release; const gate=new Promise(r=>release=r);
  await page.route('**/api/conditions?**',async r=>{await gate;await r.fulfill({json:fixture()});});
  await page.locator('#predict-safety').click();
  await expect(page.locator('#predict-safety')).toBeDisabled();
  await expect(page.locator('#data-stamp')).toContainText('Fetching');
  await page.locator('#safety-lat').fill('12.6');
  release();
  await page.waitForTimeout(150);
  await expect(page.locator('#prediction-value')).toHaveText('—');
  const missing=fixture(); missing.hourly=[];missing.prediction={available:false,reason:'Model unavailable'};
  await page.route('**/api/conditions?**',r=>r.fulfill({json:missing}));
  await page.locator('#predict-safety').click();
  await expect(page.locator('#prediction-wind-value')).toHaveText('—');
  await expect(page.locator('#data-stamp')).toContainText('Model unavailable');
  await expect(page.locator('#data-stamp')).toContainText('wind forecast unavailable');
  await page.route('**/api/conditions?**',r=>r.fulfill({status:422,json:{detail:'Choose an offshore location.'}}));
  await page.locator('#predict-safety').click();
  await expect(page.locator('#data-stamp')).toContainText('Choose an offshore location.');
  await expect(page.locator('#current-risk')).toHaveText('Unavailable');
});
test('responsive panels, tap targets and no horizontal overflow',async({page},testInfo)=>{
  await page.goto('/');
  await expect(page.locator('#prediction-value')).toHaveText('1.48');
  for(const width of [375,390,430,1440]){
    await page.setViewportSize({width,height:1000});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    const panels=await page.locator('.safety-panel').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,bottom:r.bottom};}));
    if(width<700)expect(panels[1].y).toBeGreaterThanOrEqual(panels[0].bottom);
    else expect(panels[1].y).toBe(panels[0].y);
    expect((await page.locator('#predict-safety').boundingBox()).height).toBeGreaterThanOrEqual(44);
    await page.locator('#safety').screenshot({path:testInfo.outputPath(`safety-${width}.png`)});
  }
  await expect(page.locator('#safety .chart, #safety .model-comparison, #safety #map')).toHaveCount(0);
});
