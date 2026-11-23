const { defineConfig } = require('@playwright/test');
const path = require('path');
const root = path.resolve(__dirname, '..');
module.exports = defineConfig({
  testDir: process.env.BTW_TEST_DIR || path.join(root, 'examples'),
  outputDir: process.env.BTW_OUTPUT_DIR,
  reporter: [[path.join(__dirname, 'reporter.cjs')]],
  workers: 1,
  use: {
    browserName: process.env.BTW_BROWSER || 'chromium',
    headless: process.env.BTW_HEADLESS !== 'false',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
