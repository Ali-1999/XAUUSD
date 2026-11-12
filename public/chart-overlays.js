"use strict";

const marketChart = (() => {
  const charts = window.LightweightCharts;
  const colors = { support: "#64d5ae", resistance: "#efc579", structure: "#8eb9ff", stop: "#ec8a8d" };
  const annotations = {
    rejection: { label: "H4 rejection", shape: "circle", color: colors.resistance },
    break: { label: "M15 break", shape: "square", color: colors.structure },
    retest: { label: "M15 retest", shape: "circle" },
    entry: { label: "Entry" },
    exit: { label: "Exit", shape: "square" },
    target: { label: "Target", shape: "square", color: colors.support },
    stop: { label: "Stop", shape: "square", color: colors.stop },
  };

  function create(element) {
    return charts.createChart(element, {
      autoSize: true,
      layout: { background: { color: "#111d2a" }, textColor: "#a1afbf", attributionLogo: true },
      grid: { vertLines: { color: "#1b2c3b" }, horzLines: { color: "#1b2c3b" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#283747" },
      rightPriceScale: { borderColor: "#283747" },
    });
  }

  function candles(chart, visible = true) {
    return chart.addSeries(charts.CandlestickSeries, {
      visible, upColor: colors.support, downColor: colors.stop, wickUpColor: colors.support, wickDownColor: colors.stop,
      borderVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
  }

  function barIndex(data, time) {
    let lo = 0;
    let hi = data.length;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (data[mid].time <= time) lo = mid + 1;
      else hi = mid;
    }
    return lo - 1;
  }

  function tradeLines(trade, entryTitle) {
    return [
      { price: trade.entry, title: entryTitle, color: colors.resistance },
      { price: trade.stop, title: "Stop", color: colors.stop },
      { price: trade.target, title: "Target", color: colors.support },
    ].filter((level) => Number.isFinite(level.price));
  }

  function overlay(series) {
    const layer = charts.createSeriesMarkers(series, []);
    let lines = [];
    return (data, events, levels, through) => {
      for (const line of lines) series.removePriceLine(line);
      lines = levels.map(({ price, title, color }) => series.createPriceLine({
        price, title, color, lineWidth: 1, lineStyle: charts.LineStyle.Dashed, axisLabelVisible: true,
      }));
      const points = new Map();
      for (const event of events) {
        const style = annotations[event.type];
        if (!style || event.time > through) continue;
        const anchor = event.barTime === undefined ? event.time : event.time - 1;
        const index = barIndex(data, anchor);
        if (index < 0) continue;
        const time = data[index].time;
        const buy = event.side === "buy";
        const key = `${time}/${event.type}/${event.side}`;
        const previous = points.get(key);
        if (previous) {
          previous.count += 1;
          continue;
        }
        points.set(key, {
          time, position: buy ? "belowBar" : "aboveBar", count: 1,
          shape: style.shape ?? (buy ? "arrowUp" : "arrowDown"),
          color: style.color ?? (buy ? colors.support : colors.stop),
          text: event.type === "entry" ? (buy ? "Buy entry" : "Sell entry") : style.label,
        });
      }
      layer.setMarkers([...points.values()].sort((a, b) => a.time - b.time).map(({ count, ...point }) => ({
        ...point, text: count > 1 ? `${point.text} ×${count}` : point.text,
      })));
    };
  }

  return { create, candles, overlay, barIndex, tradeLines, colors };
})();
