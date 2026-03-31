const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer'); // v23.0.0 or later

const PROMPT_TEXT = 'Generate a video from this image'; // <-- change your prompt here
const DOWNLOAD_DIR = path.resolve(__dirname, 'grok_downloads');
if (!fs.existsSync(DOWNLOAD_DIR)) fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });

(async () => {
    const browser = await puppeteer.launch({
        headless: false, // set true if you don't need to see it
        args: [`--no-sandbox`],
    });
    const page = await browser.newPage();
    const timeout = 5000;
    page.setDefaultTimeout(timeout);

    const lhApi = await import('lighthouse'); // v10.0.0 or later
    const flags = {
        screenEmulation: {
            disabled: true
        }
    }
    const config = lhApi.desktopConfig;
    const lhFlow = await lhApi.startFlow(page, {name: 'Recording grok', config, flags});
    {
        const targetPage = page;
        await targetPage.setViewport({
            width: 1365,
            height: 839
        })
    }
    await lhFlow.startNavigation();
    {
        const targetPage = page;
        await targetPage.goto('chrome://new-tab-page/');
    }
    await lhFlow.endNavigation();
    await lhFlow.startNavigation();
    {
        const targetPage = page;
        await targetPage.goto('https://grok.com/');
    }
    await lhFlow.endNavigation();
    await lhFlow.startTimespan();
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('div.pb-1 > div:nth-of-type(4) span'),
            targetPage.locator('::-p-xpath(/html/body/div[2]/div/div[1]/div/div[1]/div[2]/div[4]/ul/li/a/span)'),
            targetPage.locator(':scope >>> div.pb-1 > div:nth-of-type(4) span')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 19,
                y: 12.5,
              },
            });
    }
    // Step 1: click the upload icon to open the file picker
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Upload) >>>> ::-p-aria([role=\\"graphics-symbol\\"])'),
            targetPage.locator('form > div > div > div > div.relative path'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[2]/div/form/div/div/div/div[2]/div[1]/button/div/div/svg/path)'),
            targetPage.locator(':scope >>> form > div > div > div > div.relative path')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 5.875885009765625,
                y: 6.87591552734375,
              },
            });
    }
    // Step 2: click "Upload or drop images" button inside the picker
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Upload or drop images)'),
            targetPage.locator('div.absolute > div.shrink-0 > button'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[2]/div/form/div/div/div[2]/div[1]/button)'),
            targetPage.locator(':scope >>> div.absolute > div.shrink-0 > button'),
            targetPage.locator('::-p-text(Upload or drop imagesUpload)')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 65,
                y: 143,
              },
            });
    }
    // Step 3: fill the file input with the image path
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('input'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[2]/div/form/input)'),
            targetPage.locator(':scope >>> input')
        ])
            .setTimeout(timeout)
            .fill('C:\\fakepath\\Chapter_057_Organization and Summary_04_scene.png');
    }
    // Step 4: wait for the file attachment to complete
    // (wait for an image preview/thumbnail to appear in the form area)
    await page.waitForSelector(
        '[data-testid="drop-ui"] img, [data-testid="drop-ui"] [class*="thumbnail"], [data-testid="drop-ui"] [class*="preview"]',
        { timeout: 30000 }
    ).catch(() => {
        // fallback: if no preview selector matched, wait for network to settle
        return page.waitForNetworkIdle({ idleTime: 1000, timeout: 15000 }).catch(() => {});
    });
    console.log('File attached.');
    // Step 5: click the text area and type the prompt
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('p'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[2]/div/form/div/div/div/div[2]/div[2]/div/div/div/div/p)'),
            targetPage.locator(':scope >>> p')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 34,
                y: 14,
              },
            });
    }
    await page.keyboard.type(PROMPT_TEXT);
    console.log('Prompt typed. Waiting 3 seconds before submit...');
    // Step 6: wait 3 seconds
    await new Promise(r => setTimeout(r, 3000));
    // Step 7: click submit
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Submit) >>>> ::-p-aria([role=\\"image\\"])'),
            targetPage.locator('div.query-bar > div.absolute svg'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[2]/div/form/div/div/div[1]/div[3]/div/button/div/svg)'),
            targetPage.locator(':scope >>> div.query-bar > div.absolute svg')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 7.6629638671875,
                y: 12.66302490234375,
              },
            });
    }
    console.log('Submitted.');
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Pause) >>>> ::-p-aria([role=\\"image\\"])'),
            targetPage.locator('div.p-2\\.5 > div.flex svg'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[1]/div/main/article/div/div[2]/div[1]/div[5]/div[1]/button[1]/svg)'),
            targetPage.locator(':scope >>> div.p-2\\.5 > div.flex svg')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 3.5,
                y: 7.5,
              },
            });
    }
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Play) >>>> ::-p-aria([role=\\"image\\"])'),
            targetPage.locator('div.p-2\\.5 > div.flex svg'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[1]/div/main/article/div/div[2]/div[1]/div[5]/div[1]/button[1]/svg)'),
            targetPage.locator(':scope >>> div.p-2\\.5 > div.flex svg')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 3.5,
                y: 7.5,
              },
            });
    }
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Pause) >>>> ::-p-aria([role=\\"image\\"])'),
            targetPage.locator('div.p-2\\.5 > div.flex svg'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[1]/div/main/article/div/div[2]/div[1]/div[5]/div[1]/button[1]/svg)'),
            targetPage.locator(':scope >>> div.p-2\\.5 > div.flex svg')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 3.5,
                y: 7.5,
              },
            });
    }
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Play) >>>> ::-p-aria([role=\\"image\\"])'),
            targetPage.locator('div.p-2\\.5 > div.flex svg'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[1]/div/main/article/div/div[2]/div[1]/div[5]/div[1]/button[1]/svg)'),
            targetPage.locator(':scope >>> div.p-2\\.5 > div.flex svg')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 3.5,
                y: 7.5,
              },
            });
    }
    // Fix 2: set up CDP download tracking before clicking download
    const cdpClient = await page.createCDPSession();
    await cdpClient.send('Browser.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: DOWNLOAD_DIR,
        eventsEnabled: true,
    });
    const downloadDone = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Download timed out after 2 minutes')), 120000);
        cdpClient.on('Browser.downloadProgress', (event) => {
            if (event.state === 'completed') { clearTimeout(timer); resolve(); }
            else if (event.state === 'canceled') { clearTimeout(timer); reject(new Error('Download was canceled')); }
        });
    });
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('::-p-aria(Download[role=\\"button\\"]) >>>> ::-p-aria([role=\\"image\\"])'),
            targetPage.locator('button:nth-of-type(5) > svg'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[1]/div/main/article/div/div[4]/div[2]/button[5]/svg)'),
            targetPage.locator(':scope >>> button:nth-of-type(5) > svg')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 6.5,
                y: 6.5,
              },
            });
    }
    {
        const targetPage = page;
        await puppeteer.Locator.race([
            targetPage.locator('button:nth-of-type(5) path:nth-of-type(2)'),
            targetPage.locator('::-p-xpath(//*[@data-testid=\\"drop-ui\\"]/div/div[1]/div/main/article/div/div[4]/div[2]/button[5]/svg/path[2])'),
            targetPage.locator(':scope >>> button:nth-of-type(5) path:nth-of-type(2)')
        ])
            .setTimeout(timeout)
            .click({
              offset: {
                x: 6.3046875,
                y: 4.5,
              },
            });
    }
    // Wait for the file to finish downloading before closing
    await downloadDone;
    console.log(`Download complete. File saved to: ${DOWNLOAD_DIR}`);
    await lhFlow.endTimespan();
    const lhFlowReport = await lhFlow.generateReport();
    fs.writeFileSync(__dirname + '/flow.report.html', lhFlowReport);

    await browser.close();
    console.log('Browser closed. All done.');

})().catch(err => {
    console.error(err);
    process.exit(1);
});
