"""Run the actual page guard in isolated headless Chrome; no physical desktop input."""
import argparse
import json
from pathlib import Path
import subprocess
from browser_desktop import INSTALL

SCRIPT = r'''
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require(process.argv[1]);
const install = fs.readFileSync(0, 'utf8');
(async () => {
  const browser = await chromium.launch({executablePath:process.argv[2],headless:true});
  let count = 0;
  try {
    const context = await browser.newContext({viewport:{width:1000,height:800}});
    const page = await context.newPage();
    const setup = async () => {
      await page.goto('about:blank');
      await page.setContent(`<title>Isolated bridge test</title><style>
        body {height:4000px} button {position:fixed;left:200px;top:200px;width:200px;height:80px}
        </style><button onclick="window.clicks++">Target</button><script>window.clicks=0</script>`);
      await page.evaluate('(' + install + ')(document.querySelector("button"))');
      assert.equal(await page.evaluate(()=>typeof window.__bridgeGeometryTest),'object');
    };
    for (const [name,mutation] of [
      ['moved',"document.querySelector('button').style.left='450px'"],
      ['resized',"document.querySelector('button').style.width='260px'"],
      ['overlay',"let d=document.createElement('div');d.style='position:fixed;inset:0;z-index:99';document.body.append(d)"],
      ['scroll with fixed target',"window.scrollTo(0,100)"],
      ['removed',"document.querySelector('button').remove()"],
      ['disabled',"document.querySelector('button').disabled=true"],
      ['hidden',"document.querySelector('button').style.visibility='hidden'"],
      ['title changed',"document.title='Changed'"],
      ['same appearance replacement',"let b=document.querySelector('button');b.replaceWith(b.cloneNode(true))"],
    ]) {
      await setup();
      await page.evaluate(mutation);
      await assert.rejects(()=>page.evaluate(()=>window.__bridgeGeometryTest.read()), /recalibrate|hidden, disabled/);
      assert.equal(await page.evaluate(()=>window.clicks),0);
      console.log('PASS refusal:',name); count++;
    }
    await setup();
    await page.setViewportSize({width:900,height:700});
    await assert.rejects(()=>page.evaluate(()=>window.__bridgeGeometryTest.read()), /recalibrate|hidden, disabled/);
    console.log('PASS refusal: viewport changed'); count++;
    await page.setViewportSize({width:1000,height:800});
    await setup();
    const data = await page.evaluate(()=>window.__bridgeGeometryTest.arm());
    const [x,y,w,h] = data.bounds;
    await page.mouse.move(x+w/2,y+h/2);
    const hover = await page.evaluate(()=>window.__bridgeGeometryTest.read());
    assert.equal(hover.move.target,true);
    await page.evaluate(()=>window.__bridgeGeometryTest.prepareClick());
    await page.mouse.click(x+w/2,y+h/2);
    assert.equal(await page.evaluate(()=>window.clicks),1);
    assert.equal(await page.evaluate(()=>window.__bridgeGeometryTest.result().click.target),true);
    await page.evaluate(()=>window.__bridgeGeometryTest.cleanup());
    assert.equal(await page.evaluate(()=>typeof window.__bridgeGeometryTest),'undefined');
    console.log('PASS stable target: one browser click, trusted hover and cleanup'); count++;
    await setup();
    await page.mouse.move(300,240);
    assert.equal((await page.evaluate(()=>window.__bridgeGeometryTest.read())).move.target,true);
    await page.evaluate(()=>window.__bridgeGeometryTest.prepareClick());
    await page.evaluate(()=>document.querySelector('button').style.left='600px');
    await page.mouse.click(300,240);
    assert.equal(await page.evaluate(()=>window.clicks),0);
    assert.equal(await page.evaluate(()=>window.__bridgeGeometryTest.result().click.target),false);
    // An unrelated later click must not overwrite the first observed miss.
    await page.mouse.click(700,240);
    assert.equal(await page.evaluate(()=>window.__bridgeGeometryTest.result().click.target),false);
    await page.evaluate(()=>window.__bridgeGeometryTest.cleanup());
    console.log('PASS final-gap mutation: missed target recorded, later event cannot mask it'); count++;
    console.log(`BROWSER_GEOMETRY_PASS ${count} scenarios; browser input only, not GNOME portal`);
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
'''

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--playwright', required=True, help='Absolute path to installed playwright package')
    parser.add_argument('--chrome', default='/usr/bin/google-chrome')
    args = parser.parse_args()
    subprocess.run(['node','-e',SCRIPT,str(Path(args.playwright).resolve()),args.chrome],
                   input=INSTALL.replace('__KEY__',json.dumps('__bridgeGeometryTest')),
                   text=True,check=True,timeout=60)
