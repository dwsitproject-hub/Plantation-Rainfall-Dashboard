const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const metrics = require('../../frontend/inhouse/js/rainfall_metrics.js');
const dates = (start, count) => Array.from({length: count}, (_, index) =>
  new Date(Date.parse(start + 'T00:00:00Z') + index * 86400000).toISOString().slice(0, 10));

test('forecast excludes partial boundary weeks and retains the complete Monday–Sunday week', () => {
  const result = metrics.completeForecastWeeks(dates('2026-10-28', 14), Array(14).fill(10), '2026-10-28');
  assert.deepEqual(result, [{year: 2026, week: 45, value: 70, days: 7, start: '2026-11-02', end: '2026-11-08'}]);
});
test('missing forecast days and duplicate dates cannot masquerade as complete weeks; zero rain is valid', () => {
  const week = dates('2026-11-02', 7);
  assert.equal(metrics.completeForecastWeeks(week, [1, 1, null, 1, 1, 1, 1], '2026-11-02').length, 0);
  assert.equal(metrics.completeForecastWeeks(Array(7).fill(week[0]), Array(7).fill(2), '2026-11-02').length, 0);
  assert.equal(metrics.completeForecastWeeks(week, Array(7).fill(0), '2026-11-02')[0].value, 0);
});
test('a complete ISO week can cross December into January without wrapping the plotted year', () => {
  const result = metrics.completeForecastWeeks(dates('2026-12-28', 14), Array(14).fill(1), '2026-12-28');
  assert.equal(result.length, 1);
  assert.equal(result[0].week, 53);
  assert.equal(result[0].end, '2027-01-03');
});
test('current week uses SAP actual, external actual gap fill, and forecast once per day', () => {
  const actual = [
    {date:'2026-10-26',value:2,source:'sap'},
    {date:'2026-10-26',value:99,source:'external'},
    {date:'2026-10-27',value:3,source:'external'}
  ];
  const future = dates('2026-10-28', 5).map(date => ({date,value:10}));
  const week = metrics.completeCurrentWeek(actual, future, '2026-10-28');
  assert.equal(week.value, 55);
  assert.equal(week.week, 44);
  assert.equal(week.sapDays, 1);
  assert.equal(week.externalDays, 1);
  assert.equal(week.forecastDays, 5);
  assert.equal(week.days, 7);
  assert.equal(metrics.completeCurrentWeek(actual, future.slice(0, 4), '2026-10-28'), null);
  assert.equal(metrics.completeCurrentWeek(actual.filter(row => row.date !== '2026-10-26'), future, '2026-10-28'), null);
});
const locations = [{id: 'AA1', region: 'North'}, {id: 'BB1', region: 'North'}, {id: 'CC1', region: 'South'}];
const readings = [
  {company: 'AA', estate: '1', date: '2026-10-01', value: 10},
  {company: 'BB', estate: '1', date: '2026-10-01', value: 30},
  {company: 'AA', estate: '1', date: '2026-10-02', value: 0},
  {company: 'CC', estate: '1', date: '2026-10-02', value: null},
  {company: 'AA', estate: '1', date: '2026-09-30', value: 900},
  {company: 'AA', estate: '1', date: '2026-10-03', value: 900},
  {company: 'XX', estate: '1', date: '2026-10-02', value: 900},
];
test('regional average gives each reporting day equal weight and includes both date endpoints', () => {
  const [north, south] = metrics.regionalDailyAverages(readings, locations, '2026-10-01', '2026-10-02');
  assert.equal(north.average, 10); // mean(mean(10,30), 0), not mean(10,30,0)
  assert.equal(north.days, 2);
  assert.equal(north.estates, 2);
  assert.equal(south.average, null);
  assert.equal(south.days, 0);
});
test('same-day filters retain zero rain, respect estate selection, and reject reversed ranges', () => {
  const [north] = metrics.regionalDailyAverages(readings, locations.slice(0, 1), '2026-10-02', '2026-10-02');
  assert.equal(north.average, 0);
  assert.equal(north.estates, 1);
  assert.deepEqual(metrics.regionalDailyAverages(readings, locations, '2026-10-03', '2026-10-01'), []);
});
test('week-to-date comparison uses Monday and the same elapsed weekdays without timezone shifts', () => {
  assert.deepEqual(metrics.weekToDateWindow('2026-10-27'), {
    from: '2026-10-26', to: '2026-10-27', previousFrom: '2026-10-19', previousTo: '2026-10-20'
  });
  assert.equal(metrics.weekToDateWindow('2026-11-01').previousTo, '2026-10-25');
});

// Exercise the actual chart builders as well as the shared arithmetic.
const app = fs.readFileSync(path.join(__dirname, '../../frontend/inhouse/js/app.js'), 'utf8');
const context = vm.createContext({RainfallMetrics: metrics, state: {
  daily: [{company:'AA', estate:'1', date:'2026-10-26', value:2}, {company:'AA', estate:'1', date:'2026-10-27', value:12}],
  forecastData: {as_of_date:'2026-10-28'},
  forecastStartYear: 2020, forecastEndYear: 2025,
  forecastById: new Map([['AA1', {forecast_dates:dates('2026-10-28', 14), forecast_rainfall_mm:Array(14).fill(10)}]])
}});
for (const name of ['mean', 'isoWeekInfo', 'weeklyForecastSeries', 'averageForecastSeries', 'allRegionsWeeklySeries', 'weeklyLinePath']) {
  const start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing function ${name}`);
  const end = app.indexOf('\nfunction ', start + 1);
  vm.runInContext(app.slice(start, end < 0 ? undefined : end), context);
}
test('estate and portfolio chart builders expose only full forecast weeks', () => {
  const estate = context.weeklyForecastSeries({id:'AA1', company:'AA', estate:'1'});
  const forecast = estate.forecast.filter(row => !row.bridge);
  assert.equal(forecast.length, 2);
  assert.deepEqual(forecast.map(row => row.week), [44, 45]);
  assert.equal(forecast[0].value, 64); // Two SAP days and five forecast days.
  assert.equal(forecast[0].actualDays, 2);
  assert.equal(forecast[0].forecastDays, 5);
  assert.equal(forecast[0].days, 7);
  assert.equal(forecast[1].value, 70);
  assert.ok(!estate.sapActual.some(row => row.week === 44));
  const portfolio = context.averageForecastSeries([{id:'AA1', company:'AA', estate:'1'}]);
  assert.equal(portfolio.forecast.find(row => row.week === 44).value, 64);
  assert.equal(portfolio.forecast.find(row => row.week === 44).forecastDays, 5);
  assert.ok(portfolio.forecast.filter(row => !row.bridge).every(row => row.days === 7));
});
test('regional context includes complete actual plus forecast current week and suppresses incomplete future weeks', () => {
  context.state.allRegionsData = {lastActual:'2026-10-27', weekly:[], regions:[{key:'Overall Indonesia',region:'Indonesia'}],
    rainDaily: dates('2026-10-19', 23).map(date => ({region:'Overall Indonesia',date,rain:1,type:date<='2026-10-27'?'historical_actual':'forecast'}))};
  const series = context.allRegionsWeeklySeries('Overall Indonesia');
  assert.equal(series.rows.find(row => row.week === 43).current, 7);
  assert.equal(series.rows.find(row => row.week === 44).current, 7);
  assert.equal(series.rows.find(row => row.week === 44).actualDays, 2);
  assert.equal(series.rows.find(row => row.week === 44).forecastDays, 5);
  assert.equal(series.rows.find(row => row.week === 45).current, 7);
  assert.equal(series.rows.find(row => row.week === 46).current, null);
  assert.equal(context.weeklyLinePath([{week:43,value:7},{week:45,value:7}], x=>x, y=>y), 'M43.0,7.0 M45.0,7.0');
});
