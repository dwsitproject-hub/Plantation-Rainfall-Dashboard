(function (root, factory) {
  const metrics = factory();
  if (typeof module === 'object' && module.exports) module.exports = metrics;
  else root.RainfallMetrics = metrics;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') &&
    Number.isFinite(Date.parse(value + 'T00:00:00Z')) &&
    new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
  const finiteValue = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const mean = values => values.reduce((sum, value) => sum + value, 0) / values.length;

  function isoWeek(dateText) {
    const day = new Date(dateText + 'T00:00:00Z');
    day.setUTCDate(day.getUTCDate() + 4 - (day.getUTCDay() || 7));
    const year = day.getUTCFullYear();
    const start = new Date(Date.UTC(year, 0, 1));
    return { year, week: Math.ceil((((day - start) / 86400000) + 1) / 7) };
  }

  function completeForecastWeeks(dates, values, today, year = isoWeek(today).year) {
    const buckets = new Map();
    dates.forEach((date, index) => {
      if (!validDate(date) || date < today || !finiteValue(values[index])) return;
      const info = isoWeek(date);
      if (info.year !== year) return;
      if (!buckets.has(info.week)) buckets.set(info.week, new Map());
      buckets.get(info.week).set(date, Number(values[index]));
    });
    return [...buckets].filter(([, days]) => days.size === 7).map(([week, days]) => {
      const ordered = [...days].sort(([a], [b]) => a.localeCompare(b));
      return { year, week, value: ordered.reduce((sum, [, value]) => sum + value, 0),
        days: 7, start: ordered[0][0], end: ordered[6][0] };
    }).sort((a, b) => a.week - b.week);
  }

  function completeCurrentWeek(actualRows, forecastRows, today) {
    if (!validDate(today)) return null;
    const day = new Date(today + 'T00:00:00Z');
    const monday = new Date(day);
    monday.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
    const actual = new Map(), forecast = new Map();
    actualRows.forEach(row => {
      if (!validDate(row.date) || row.date > today || !finiteValue(row.value)) return;
      const old = actual.get(row.date);
      if (!old || row.source === 'sap') actual.set(row.date, { value: Number(row.value), source: row.source });
    });
    forecastRows.forEach(row => {
      if (validDate(row.date) && row.date >= today && finiteValue(row.value)) forecast.set(row.date, Number(row.value));
    });
    const result = { year: isoWeek(today).year, week: isoWeek(today).week, value: 0,
      days: 7, actualDays: 0, sapDays: 0, externalDays: 0, forecastDays: 0 };
    for (let index = 0; index < 7; index++) {
      const date = new Date(monday.getTime() + index * 86400000).toISOString().slice(0, 10);
      if (index === 0) result.start = date;
      if (index === 6) result.end = date;
      if (actual.has(date)) {
        const row = actual.get(date);
        result.value += row.value;
        result.actualDays++;
        if (row.source === 'sap') result.sapDays++;
        else result.externalDays++;
      } else if (forecast.has(date)) {
        result.value += forecast.get(date);
        result.forecastDays++;
      } else return null;
    }
    return result;
  }

  function regionalDailyAverages(rows, locations, from, to) {
    if (!validDate(from) || !validDate(to) || from > to) return [];
    const estates = new Map(locations.map(location => [location.id, location]));
    const regions = new Map();
    locations.forEach(location => {
      if (!regions.has(location.region)) regions.set(location.region, { estates: new Set(), days: new Map(), reporting: new Set() });
      regions.get(location.region).estates.add(location.id);
    });
    rows.forEach(row => {
      const id = `${row.company}${row.estate}`, location = estates.get(id);
      if (!location || !validDate(row.date) || row.date < from || row.date > to || !finiteValue(row.value)) return;
      const region = regions.get(location.region);
      if (!region.days.has(row.date)) region.days.set(row.date, new Map());
      region.days.get(row.date).set(id, Number(row.value));
      region.reporting.add(id);
    });
    return [...regions].sort(([a], [b]) => a.localeCompare(b)).map(([region, item]) => {
      const days = [...item.days.keys()].sort();
      const dailyMeans = days.map(day => mean([...item.days.get(day).values()]));
      return { region, estates: item.estates.size, reportingEstates: item.reporting.size,
        days: days.length, average: dailyMeans.length ? mean(dailyMeans) : null, latestDate: days.at(-1) || null };
    });
  }

  function weekToDateWindow(latest) {
    if (!validDate(latest)) return null;
    const end = new Date(latest + 'T00:00:00Z');
    const offset = (end.getUTCDay() + 6) % 7;
    const shift = days => new Date(end.getTime() + days * 86400000).toISOString().slice(0, 10);
    return { from: shift(-offset), to: latest, previousFrom: shift(-offset - 7), previousTo: shift(-7) };
  }
  return { completeForecastWeeks, completeCurrentWeek, regionalDailyAverages, weekToDateWindow };
});
