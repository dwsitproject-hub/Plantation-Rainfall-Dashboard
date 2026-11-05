// All Regions overview (frontend/js/dashboard.js): chart tooltips must cope with missing readings.
// Production error 3 Nov 2026 (Sentinel report 46d44f55): hovering the monthly history before 2017,
// where soil moisture has no data, threw "Cannot read properties of null (reading 'toFixed')".
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '../../frontend/js/dashboard.js'), 'utf8');

function fakeElement() {
  return {
    innerHTML: '', textContent: '', value: '', dataset: {}, style: {}, children: [],
    offsetWidth: 120, offsetHeight: 60,
    classList: {add() {}, remove() {}, toggle() {}},
    setAttribute() {}, appendChild(child) { this.children.push(child); },
    addEventListener() {}, querySelectorAll() { return []; },
    getBoundingClientRect: () => ({left: 0, top: 0, width: 1000, height: 200}),
  };
}

function loadDashboard() {
  const elements = new Map();
  const document = {
    body: fakeElement(),
    createElement: () => fakeElement(),
    querySelector: selector => {
      if (!elements.has(selector)) elements.set(selector, fakeElement());
      return elements.get(selector);
    },
    querySelectorAll: () => [],
    addEventListener() {},
  };
  const context = vm.createContext({
    document, innerWidth: 1600, innerHeight: 900, console,
    fetch: () => new Promise(() => {}),  // the page's own data load never finishes; the test sets DATA
    addEventListener() {}, setTimeout, clearTimeout,
  });
  vm.runInContext(SOURCE, context);
  return {context, elements, run: code => vm.runInContext(code, context)};
}

function sweep(page, selector) {
  // Move the pointer across the whole chart; return any errors and the tooltip at each position.
  const element = page.elements.get(selector);
  const tips = [], errors = [];
  for (let k = 0; k <= 100; k++) {
    try {
      element.onpointermove({clientX: k * 10, clientY: 100});
      tips.push(page.run('hoverTip.innerHTML'));
    } catch (error) {
      errors.push(error.message);
    }
  }
  return {tips, errors};
}

const months = Array.from({length: 24}, (_, i) => `${2015 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`);
const DATA = {
  monthly: months.map((month, i) => ({
    region: 'Overall Indonesia', month, rain: 150 + i,
    temperature: i === 3 ? null : 27.1,
    soil: month < '2016-07' ? null : 0.312,  // soil moisture history starts later than rainfall
  })),
  daily: Array.from({length: 20}, (_, i) => ({
    region: 'Overall Indonesia', date: `2026-10-${String(i + 1).padStart(2, '0')}`,
    type: i < 14 ? 'historical_actual' : 'forecast', rain: i % 3, temperature: 27,
    soil: i === 5 || i === 6 ? null : 0.30 + i / 1000,  // two missing days
  })),
};

test('monthly history tooltip shows a dash for missing soil moisture instead of throwing', () => {
  const page = loadDashboard();
  page.run(`DATA = ${JSON.stringify(DATA)}; drawHistory('Overall Indonesia');`);
  const {tips, errors} = sweep(page, '#historyChart');
  assert.deepEqual(errors, []);
  assert.match(tips[0], /2015-01/);
  assert.match(tips[0], /Average soil moisture <b>— /);
  assert.match(tips.at(-1), /Average soil moisture <b>0\.312 /);
  assert.ok(tips.some(tip => /Average temperature <b>— /.test(tip)), 'missing temperature is a dash, not 0');
});

test('daily chart: no errors, soil line breaks at missing days and its scale ignores them', () => {
  const page = loadDashboard();
  page.run(`DATA = ${JSON.stringify(DATA)}; drawDaily('Overall Indonesia');`);
  const {tips, errors} = sweep(page, '#chart');
  assert.deepEqual(errors, []);
  assert.ok(tips.some(tip => /Soil moisture <b>— /.test(tip)));
  const svg = page.elements.get('#chart').innerHTML;
  const soilPath = svg.match(/class="soil-line" d="([^"]*)"/)[1];
  assert.equal((soilPath.match(/M/g) || []).length, 2, 'one segment before and one after the gap');
  assert.doesNotMatch(svg, /NaN/);
  // Scale from real values only: 0.300 - 0.02 and 0.319 + 0.02 (a missing day counted as 0 would give -0.02).
  assert.match(svg, />0\.34<\/text>/);
  assert.match(svg, />0\.28<\/text>/);
});
