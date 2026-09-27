const {test,expect}=require('@playwright/test');
const row=()=>{
  const now=new Date();now.setUTCMinutes(0,0,0);
  const hourly=Array.from({length:55},(_,i)=>({time:new Date(+now+(i-6)*3600000).toISOString(),wave_m:.8+i*.004,wind_ms:4,period_s:6}));
  const risk={label:'safe',label_id:0,class_scores:{safe:.9,moderate:.09,danger:.01}};
  return {current:hourly[6],hourly,current_risk:{label:'safe',explanation:'Test-derived safe class.'},fetched_at:new Date().toISOString(),response_ms:35,prediction:{available:true,wave_m:.9,lower_m:.7,upper_m:1.1,target_time:hourly[12].time,selected_model:'random_forest',models:{random_forest:{wave_m:.9},xgboost:{wave_m:1}},risk_prediction:{available:true,selected_model:'random_forest',...risk,models:{random_forest:risk,xgboost:risk}}}};
};
const route=()=>({routes:[{name:'Shortest feasible',label:'safe',coordinates:[[13.13,80.34],[13.25,80.65]],distance_km:38,duration_hours:2.6,max_wave_m:1.1,max_wind_ms:5,exposure_index_km:12,peak_risk_ratio:.55},{name:'Safest then shortest',label:'safe',coordinates:[[13.13,80.34],[13.20,80.5],[13.25,80.65]],distance_km:39,duration_hours:2.7,max_wave_m:1,max_wind_ms:5,exposure_index_km:10,peak_risk_ratio:.5}],samples:[],message:'Test route fixture.',response_ms:120,forecast_window_hours:12,ml_used:true,fetched_at:new Date().toISOString()});
test.beforeEach(async({page})=>{
  await page.route('**/api/conditions?**',r=>r.fulfill({json:row()}));
  await page.route('**/api/routes',r=>r.fulfill({json:route()}));
});
test('mobile layout, conditions, route comparison and invalidation',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await expect(page.locator('#wave-value')).not.toHaveText('—');
  await expect(page.locator('#current-risk')).toHaveText('Safe');
  await expect(page.locator('#forecast-risk')).toHaveText('Safe');
  await expect(page.locator('#prediction-wind-value')).toHaveText('4.0');
  await expect(page.locator('#model-comparison')).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await page.locator('[data-tab=plan]').click();
  await page.locator('#plan-route').click();
  await expect(page.locator('.route-card')).toHaveCount(2);
  await page.locator('#end-lat').fill('13.3');
  await expect(page.locator('.route-card')).toHaveCount(0);
  await expect(page.locator('#route-status')).toContainText('Inputs changed');
  await page.locator('#science > summary').click();
  await expect(page.locator('#evaluation')).toContainText('2025 held-out test');
  expect(errors).toEqual([]);
});
test('safety error clears safety results independently',async({page})=>{
  await page.goto('/');
  await expect(page.locator('#wave-value')).not.toHaveText('—');
  await page.locator('[data-tab=plan]').click();
  await page.locator('#plan-route').click();
  await expect(page.locator('.route-card')).toHaveCount(2);
  await page.route('**/api/conditions?**',r=>r.fulfill({status:503,json:{detail:'Ocean data unavailable'}}));
  await page.locator('[data-tab=safety]').click();
  await page.locator('#predict-safety').click();
  await expect(page.locator('#data-stamp')).toHaveText('Ocean data unavailable');
  await expect(page.locator('#wave-value')).toHaveText('—');
  await expect(page.locator('#forecast-risk')).toHaveText('Unavailable');
  await expect(page.locator('.route-card')).toHaveCount(2);
});

test('model selection sends location and invalidates route advice',async({page})=>{
  let requested='';
  await page.route('**/api/conditions?**',async r=>{requested=r.request().url();await r.fulfill({json:row()});});
  await page.goto('/');
  await expect(page.locator('#current-risk')).toHaveText('Safe');
  await page.locator('[data-tab=plan]').click();
  await page.locator('#plan-route').click();
  await expect(page.locator('.route-card')).toHaveCount(2);
  await page.selectOption('#model-choice','xgboost');
  await expect(page.locator('.route-card')).toHaveCount(0);
  await page.locator('[data-tab=safety]').click();
  await page.selectOption('#prediction-model','xgboost');
  await page.locator('#predict-safety').click();
  await expect.poll(()=>requested).toContain('model=xgboost');
  expect(requested).toContain('latitude=13.13');
});
test('offline blocks planning and installation help is available',async({page,context})=>{
  await page.goto('/');
  await expect(page.locator('#wave-value')).not.toHaveText('—');
  await context.setOffline(true);
  await expect(page.locator('#plan-route')).toBeDisabled();
  await expect(page.locator('#connection')).toContainText('Offline');
  await expect(page.locator('#wave-value')).toHaveText('—');
  await context.setOffline(false);
  await expect(page.locator('#plan-route')).toBeEnabled();
  await page.locator('#guide > summary').click();
  await expect(page.locator('#guide')).toContainText('Add to Home Screen');
});
test('in-flight response cannot restore routes after input changes',async({page})=>{
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  await page.route('**/api/routes',async r=>{await gate;await r.fulfill({json:route()});});
  await page.goto('/');
  await expect(page.locator('#wave-value')).not.toHaveText('—');
  await page.locator('[data-tab=plan]').click();
  await page.locator('#plan-route').click();
  await expect(page.locator('#plan-route')).toBeDisabled();
  await page.locator('#end-lat').fill('13.4');
  release();
  await page.waitForTimeout(200);
  await expect(page.locator('.route-card')).toHaveCount(0);
});

test('entered coordinates fetch live sea data for safety prediction',async({page})=>{
  let requested;
  await page.route('**/api/conditions?**',r=>{requested=new URL(r.request().url());return r.fulfill({json:row()});});
  await page.goto('/');
  await expect(page.locator('#forecast-risk')).toHaveText('Safe');
  await page.locator('#safety-lat').fill('12.5');
  await page.locator('#safety-lon').fill('80.5');
  await expect(page.locator('#forecast-risk')).toHaveText('Unavailable');
  await page.locator('#predict-safety').click();
  await expect(page.locator('#forecast-risk')).toHaveText('Safe');
  expect(requested.searchParams.get('latitude')).toBe('12.5');
  expect(requested.searchParams.get('longitude')).toBe('80.5');
  await expect(page.locator('#safety-location')).toHaveText('12.5°N • 80.5°E');
  await expect(page.locator('#forecast-panel')).toBeVisible();
  await expect(page.locator('#manual-wave')).toHaveCount(0);
});
test('route border preview and live GPS crossing, poor accuracy and stop',async({page})=>{
  await page.addInitScript(()=>{
    navigator.geolocation.watchPosition=(success)=>{window.gpsFix=success;return 7;};
    navigator.geolocation.clearWatch=()=>{};
  });
  await page.goto('/');
  await page.locator('[data-tab=plan]').click();
  for(const [id,value] of Object.entries({'start-lat':'8.5','start-lon':'78.95','end-lat':'8.5','end-lon':'79.15'}))await page.locator('#'+id).fill(value);
  await expect(page.locator('#route-border-warning')).toContainText(/crosses|Sri Lankan/);
  await page.locator('#start-tracking').click();
  const fix=async(longitude,accuracy=10)=>page.evaluate(({longitude,accuracy})=>window.gpsFix({coords:{latitude:8.5,longitude,accuracy},timestamp:Date.now()}),{longitude,accuracy});
  await fix(78.95);
  await expect(page.locator('#boat-warning')).toContainText('Indian side');
  await fix(79.15);
  await expect(page.locator('#boat-warning')).toContainText('Border crossing detected');
  await page.locator('[data-tab=safety]').click();
  await expect(page.locator('#boat-warning')).toBeVisible();
  await fix(79.15,2000);
  await expect(page.locator('#boat-warning')).toContainText('too poor');
  await page.locator('[data-tab=plan]').click();
  await page.locator('#stop-tracking').click();
  await expect(page.locator('#gps-status')).toContainText('Stopped');
});
