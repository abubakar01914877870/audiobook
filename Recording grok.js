const fs = require('fs');
const puppeteer = require('puppeteer'); // v23.0.0 or later

(async () => {
    const browser = await puppeteer.launch();
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
                x: 133,
                y: 12,
              },
            });
    }
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
    await lhFlow.endTimespan();
    const lhFlowReport = await lhFlow.generateReport();
    fs.writeFileSync(__dirname + '/flow.report.html', lhFlowReport)

    await browser.close();

})().catch(err => {
    console.error(err);
    process.exit(1);
});
