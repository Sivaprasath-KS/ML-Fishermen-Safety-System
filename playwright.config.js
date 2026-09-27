const { defineConfig, devices } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser', timeout: 30000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:8000', launchOptions: {executablePath:'/usr/bin/google-chrome', args:['--no-sandbox']}, serviceWorkers:'block' },
  projects: [
    {name:'desktop',use:{viewport:{width:1440,height:1000}}},
    {name:'mobile',use:{...devices['iPhone 13'],defaultBrowserType:'chromium'}}
  ]
});
