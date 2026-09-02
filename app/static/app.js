const state = { dashboard: null, selected: null, chart: null, period: '1d', chartWindow: null,
  colorConvention: localStorage.getItem('rooftop-color-convention') || 'redUp', graph: null, chatSession: null,
  chartDrag: null, strategyFocus:null, factorFocus:null, realtimeBusy:false };
const $ = (selector) => document.querySelector(selector);
const money = (value, digits = 2) => Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const pct = (value) => `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
const safe = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const chartTrendColor = value => Number(value)>=0?(state.colorConvention==='redUp'?'#ff5e6c':'#31d6a0'):(state.colorConvention==='redUp'?'#31d6a0':'#ff5e6c');

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function normalizeChartPayload(payload, requestedPeriod) {
  if (Array.isArray(payload.periods) && Array.isArray(payload.series)) return payload;
  // Compatibility with a Python process started before chart API v2.
  if (Array.isArray(payload.prices)) {
    const series = payload.prices.map((point, index, rows) => {
      const closes = rows.slice(Math.max(0, index - 4), index + 1).map(row => Number(row.close));
      return {...point, time:point.trade_date, amount:point.amount ?? null,
        ma5:closes.length === 5 ? closes.reduce((sum, value) => sum + value, 0) / 5 : null};
    });
    return {...payload, quote:payload.quote || null, periods:[{key:'1d', label:'日K（旧后台）'}],
      period:'1d', period_label:'日K', chart_type:'candlestick', series,
      supports_ma5:true, legacy_backend:true, requested_period:requestedPeriod};
  }
  throw new Error('图表接口响应不完整：缺少 periods/series，请重启本地后台');
}

function normalizeDashboard(payload) {
  const portfolio = payload.portfolio || {};
  return {...payload, meta:payload.meta || {}, markets:Array.isArray(payload.markets) ? payload.markets : [],
    hypotheses:Array.isArray(payload.hypotheses) ? payload.hypotheses : [],
    evidence:Array.isArray(payload.evidence) ? payload.evidence : [],
    risk_policies:Array.isArray(payload.risk_policies) ? payload.risk_policies : [],
    strategy_lab:{...(payload.strategy_lab||{}),
      strategies:Array.isArray(payload.strategy_lab?.strategies) ? payload.strategy_lab.strategies : [],
      factors:Array.isArray(payload.strategy_lab?.factors) ? payload.strategy_lab.factors : [],
      backtests:Array.isArray(payload.strategy_lab?.backtests) ? payload.strategy_lab.backtests : []},
    intelligence_sources:Array.isArray(payload.intelligence_sources) ? payload.intelligence_sources : [],
    source_health:Array.isArray(payload.source_health) ? payload.source_health : [],
    portfolio:{...portfolio, positions:Array.isArray(portfolio.positions) ? portfolio.positions : [],
      market_value:Number(portfolio.market_value || 0), unrealized_pnl:Number(portfolio.unrealized_pnl || 0)}};
}

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 2600);
}

function renderMarkets(items) {
  $('#marketTape').innerHTML = items.map(item => {
    const change = Number(item.change_pct);
    const source = item.source ? `${item.source} · ` : '';
    const observed = item.trade_date ? String(item.trade_date).slice(5, 16).replace('T', ' ') : '未知时点';
    return `<div class="tape-item" title="${safe(source + observed)}"><span>${safe(item.name)}</span><strong>${money(item.price, item.price < 10 ? 3 : 2)} <em class="${change >= 0 ? 'positive' : 'negative'}">${pct(change)}</em></strong><small>${safe(source + observed)}</small></div>`;
  }).join('');
}

function renderSummary(portfolio) {
  $('#portfolioValue').textContent = `¥ ${money(portfolio.market_value)}`;
  const pnl = $('#portfolioPnl');
  pnl.textContent = `${portfolio.unrealized_pnl >= 0 ? '+' : ''}¥ ${money(portfolio.unrealized_pnl)}`;
  pnl.className = portfolio.unrealized_pnl >= 0 ? 'positive' : 'negative';
  const alerting = portfolio.positions.filter(p => p.discipline.severity !== 'normal');
  $('#disciplineState').textContent = alerting.length ? `${alerting.length} 项需复核` : '纪律区间内';
  $('#disciplineDetail').textContent = alerting.length ? '出现研究/止盈止损提示' : '未触发当前规则阈值';
}

function renderPositions(positions) {
  $('#positionCount').textContent = `${positions.length} 项`;
  $('#positionsBody').innerHTML = positions.map(position => `
    <tr>
      <td><strong>${safe(position.name)}</strong><small>${safe(position.symbol)}</small></td>
      <td>${money(position.quantity, 0)}</td>
      <td>¥ ${money(position.market_value)}</td>
      <td class="${position.pnl_pct >= 0 ? 'positive' : 'negative'}">${pct(position.pnl_pct)}</td>
      <td><span class="status-tag">${safe(actionLabel(position.discipline.action))}</span></td>
    </tr>`).join('');
}

function renderHypotheses(hypotheses) {
  $('#hypothesisList').innerHTML = hypotheses.map(h => `
    <article class="hypothesis"><h3><span>（待验证观点）</span>${safe(h.title)}</h3><p>${safe(h.statement)}</p></article>`).join('');
}

function actionLabel(action) {
  return ({SELL_REVIEW:'卖出红线复核',TAKE_PROFIT_REVIEW:'止盈复核',BUY_RESEARCH:'绿线研究区',HOLD_DISCIPLINE:'纪律区间内'})[action] || action;
}

async function selectAsset(symbol) {
  const position = state.dashboard.portfolio.positions.find(p => p.symbol === symbol);
  const market = state.dashboard.markets.find(p => p.symbol === symbol);
  state.selected = position || {symbol, name: market?.name || symbol, current_price: market?.price,
    pnl_pct: market?.change_pct || 0, discipline: {action:'HOLD_DISCIPLINE',severity:'normal',reason:'非持仓标的，仅展示行情'}};
  const chartResponse = await request(`/api/assets/${encodeURIComponent(symbol)}/chart?period=${encodeURIComponent(state.period)}`);
  state.chart = normalizeChartPayload(chartResponse, state.period);
  if (state.chart.legacy_backend) {
    state.period = '1d';
    $('#warning').textContent = '检测到仍在运行的旧版后台：当前已安全降级为日K。请停止旧进程并重新运行 run.ps1，以启用全部周期、搜索、事件图和 Agent Chat。';
    $('#warning').classList.add('negative');
  }
  $('#assetName').textContent = state.selected.name;
  const livePrice = state.chart.quote ? state.chart.quote.price : state.selected.current_price;
  $('#assetPrice').textContent = `¥ ${money(livePrice, 3)}`;
  const displayChange = state.chart.quote ? state.chart.quote.change_pct : state.selected.pnl_pct;
  $('#assetPnl').textContent = pct(displayChange || 0);
  $('#assetPnl').className = 'change-pill';
  $('#assetPnl').style.color = chartTrendColor(displayChange || 0);
  renderPeriods(state.chart.periods);
  renderChart(state.chart);
  renderDiscipline(position || state.selected);
  $('#recheckButton').disabled = !position;
}

async function refreshRealtimeOverview(){
  if(state.realtimeBusy||document.hidden||!state.dashboard)return;
  state.realtimeBusy=true;
  try{
    const symbols=[...new Set([...state.dashboard.portfolio.positions.map(item=>item.symbol),...state.dashboard.markets.map(item=>item.symbol)])].filter(symbol=>/^(\d{6}|\d{6}\.(SH|SZ))$/.test(symbol));
    if(symbols.length){const live=await request(`/api/market/quotes?symbols=${encodeURIComponent(symbols.join(','))}`),bySymbol=new Map(live.quotes.map(item=>[item.asset_symbol,item]));state.dashboard.markets=state.dashboard.markets.map(item=>{const quote=bySymbol.get(item.symbol);return quote?{...item,price:quote.price,change_pct:quote.change_pct,trade_date:quote.observed_at,source:quote.source}:item;});renderMarkets(state.dashboard.markets);}
    if(state.selected?.symbol&&document.querySelector('#overviewView.active')){const response=normalizeChartPayload(await request(`/api/assets/${encodeURIComponent(state.selected.symbol)}/chart?period=${encodeURIComponent(state.period)}`),state.period),wasAtEnd=!state.chartWindow||state.chartWindow.end>=(state.chart?.series?.length||0);state.chart=response;if(wasAtEnd&&state.chartWindow){const span=state.chartWindow.end-state.chartWindow.start;state.chartWindow.end=response.series.length;state.chartWindow.start=Math.max(0,response.series.length-span);}renderChart(response);const livePrice=response.quote?.price??response.series.at(-1)?.close,change=response.quote?.change_pct??response.series.at(-1)?.change_pct;$('#assetPrice').textContent=`¥ ${money(livePrice,3)}`;$('#assetPnl').textContent=pct(change||0);$('#assetPnl').style.color=chartTrendColor(change||0);$('#asOf').textContent=response.quote?.observed_at||response.series.at(-1)?.time||'—';}
  }catch(error){console.warn('realtime overview refresh failed',error);}finally{state.realtimeBusy=false;}
}

function renderDiscipline(position) {
  const card = $('#disciplineCard');
  card.className = `discipline-card ${position.discipline.severity}`;
  card.innerHTML = `<div class="status">${safe(actionLabel(position.discipline.action))}</div><p>（模型输出）${safe(position.discipline.reason)}。执行任何操作前仍需确认行情时效、滑点、基本面与证据覆盖。</p>`;
  const lines = state.chart.lines;
  if (!lines) {
    card.innerHTML = `<div class="status">行情观察</div><p>该标的不在当前持仓中，因此不生成成本、止盈或止损纪律线。</p>`;
    $('#lineList').innerHTML = '';
    return;
  }
  const rows = [
    ['#ff5e6c','止损红线',lines.stop_loss], ['#68a9ff','持仓成本线',lines.cost],
    ['#31d6a0','绿线研究价',lines.buy_watch], ['#f5bc60','止盈目标线',lines.take_profit]
  ];
  $('#lineList').innerHTML = rows.map(row => `<div class="line-row"><i style="background:${row[0]}"></i><span>${row[1]}</span><strong>¥ ${money(row[2], 3)}</strong></div>`).join('');
}

function renderPeriods(periods) {
  $('#periodSelector').innerHTML = periods.map(p => `<button class="period-button ${p.key === state.period ? 'active' : ''}" data-period="${safe(p.key)}">${safe(p.label)}</button>`).join('');
  document.querySelectorAll('.period-button').forEach(button => button.onclick = async () => {
    state.period = button.dataset.period;
    state.chartWindow = null;
    await selectAsset(state.selected.symbol);
  });
}

const compactNumber = value => { const n=Number(value||0),a=Math.abs(n); if(a>=1e8)return `${(n/1e8).toFixed(2)}亿`;if(a>=1e4)return `${(n/1e4).toFixed(2)}万`;return money(n,0); };
const signed = value => value==null?'--':`${Number(value)>=0?'+':''}${Number(value).toFixed(2)}%`;

function buildVolumeProfile(series, min, max, bins=120) {
  const step=(max-min)/bins || 1, profile=Array.from({length:bins},(_,i)=>({price:min+(i+.5)*step,volume:0}));
  series.forEach(point=>{const low=Math.max(0,Math.min(bins-1,Math.floor((Number(point.low)-min)/step))),high=Math.max(low,Math.min(bins-1,Math.floor((Number(point.high)-min)/step))),share=Number(point.volume||0)/(high-low+1||1);for(let i=low;i<=high;i++)profile[i].volume+=share;});
  const total=profile.reduce((sum,item)=>sum+item.volume,0)||1;
  const quantile=q=>{let sum=0;for(const item of profile){sum+=item.volume;if(sum/total>=q)return item.price;}return profile[profile.length-1].price;};
  const ranges={p70:[quantile(.15),quantile(.85)],p90:[quantile(.05),quantile(.95)]};
  const concentration=range=>(range[1]-range[0])/Math.max((range[1]+range[0])/2,.000001)*100;
  return {profile,ranges,c70:concentration(ranges.p70),c90:concentration(ranges.p90),maxVolume:Math.max(...profile.map(item=>item.volume),1)};
}

function hoverStats(point, absoluteIndex, payload) {
  const full=payload.series,turnover=point.turnover_rate ?? (absoluteIndex===full.length-1?payload.quote?.turnover_rate:null);
  const rangeStart=state.chartWindow?.start||0,baseClose=Number(full[rangeStart]?.close);
  const cumulative=baseClose?(Number(point.close)/baseClose-1)*100:null;
  const amountPrefix=point.amount_estimated?'≈':'';
  $('#chartHoverStats').innerHTML=`<strong>${safe(String(point.time).replace('T',' ').slice(0,19))}</strong><span>涨跌幅 <b style="color:${chartTrendColor(point.change_pct)}">${signed(point.change_pct)}</b></span><span>开 <b>${money(point.open,3)}</b></span><span>高 <b>${money(point.high,3)}</b></span><span>低 <b>${money(point.low,3)}</b></span><span>换 <b>${turnover==null?'--':Number(turnover).toFixed(2)+'%'}</b></span><span>量 <b>${compactNumber(point.volume)}</b></span><span>额 <b>${amountPrefix}${point.amount?compactNumber(point.amount):'--'}</b></span><span>至今涨幅 <b style="color:${chartTrendColor(cumulative)}">${signed(cumulative)}</b></span>`;
}

function indicatorSvg(series, scale, kind, upColor, downColor) {
  const {width,height,left,right,x}=scale,plotRight=width-right,plotWidth=plotRight-left,innerTop=18,innerBottom=8,innerHeight=height-innerTop-innerBottom;
  let content='',labels='';
  if(kind==='amount'){
    const values=series.map(p=>Number(p.amount||0)),max=Math.max(...values,1),bar=Math.max(1,Math.min(8,plotWidth/series.length*.72));
    content=series.map((p,i)=>{const h=values[i]/max*innerHeight,color=Number(p.close)>=Number(p.open)?upColor:downColor;return `<rect x="${x(i)-bar/2}" y="${height-innerBottom-h}" width="${bar}" height="${h}" fill="${color}" opacity=".72"/>`;}).join('');
    labels=`<text x="${left}" y="11">成交额　峰值 ${compactNumber(max)}</text>`;
  }else if(kind==='kdj'){
    const values=series.flatMap(p=>[Number(p.kdj_k??50),Number(p.kdj_d??50),Number(p.kdj_j??50)]),min=Math.min(-20,...values),max=Math.max(120,...values),yy=v=>innerTop+(max-v)*innerHeight/(max-min||1),line=(key,color)=>`<polyline fill="none" stroke="${color}" stroke-width="1.2" points="${series.map((p,i)=>`${x(i)},${yy(Number(p[key]??50))}`).join(' ')}"/>`;
    content=`<line class="indicator-grid" x1="${left}" y1="${yy(80)}" x2="${plotRight}" y2="${yy(80)}"/><line class="indicator-grid" x1="${left}" y1="${yy(20)}" x2="${plotRight}" y2="${yy(20)}"/>${line('kdj_k','#f5bc60')}${line('kdj_d','#68a9ff')}${line('kdj_j','#d889ff')}`;
    labels=`<text x="${left}" y="11">KDJ　<tspan fill="#f5bc60">K</tspan> <tspan fill="#68a9ff">D</tspan> <tspan fill="#d889ff">J</tspan></text>`;
  }else{
    const values=series.flatMap(p=>[Number(p.macd_dif||0),Number(p.macd_dea||0),Number(p.macd_hist||0)]),min=Math.min(0,...values),max=Math.max(0,...values),yy=v=>innerTop+(max-v)*innerHeight/(max-min||1),zero=yy(0),bar=Math.max(1,Math.min(7,plotWidth/series.length*.62)),line=(key,color)=>`<polyline fill="none" stroke="${color}" stroke-width="1.1" points="${series.map((p,i)=>`${x(i)},${yy(Number(p[key]||0))}`).join(' ')}"/>`;
    content=`<line class="indicator-grid" x1="${left}" y1="${zero}" x2="${plotRight}" y2="${zero}"/>${series.map((p,i)=>{const value=Number(p.macd_hist||0),yv=yy(value);return `<rect x="${x(i)-bar/2}" y="${Math.min(zero,yv)}" width="${bar}" height="${Math.max(1,Math.abs(zero-yv))}" fill="${value>=0?upColor:downColor}" opacity=".7"/>`;}).join('')}${line('macd_dif','#f5bc60')}${line('macd_dea','#68a9ff')}`;
    labels=`<text x="${left}" y="11">MACD(12,26,9)　<tspan fill="#f5bc60">DIF</tspan> <tspan fill="#68a9ff">DEA</tspan></text>`;
  }
  return `<div class="indicator-panel"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${labels}${content}<line class="linked-crosshair crosshair" y1="0" y2="${height}" visibility="hidden"/></svg></div>`;
}

function renderChart(payload) {
  const full=payload.series||[];
  if(!full.length){$('#chart').innerHTML='<div class="empty-state">该周期暂无本地数据，请先刷新该标的。</div>';return;}
  const defaultVisible=payload.chart_type==='line'?Math.min(full.length,state.period==='5d'?600:242):Math.min(full.length,90);
  if(!state.chartWindow||state.chartWindow.key!==`${payload.asset.symbol}:${payload.period}`)state.chartWindow={key:`${payload.asset.symbol}:${payload.period}`,start:Math.max(0,full.length-defaultVisible),end:full.length};
  const windowState=state.chartWindow;windowState.end=Math.min(full.length,windowState.end);windowState.start=Math.max(0,Math.min(windowState.start,windowState.end-2));
  const series=full.slice(windowState.start,windowState.end),width=Math.max(760,Math.round($('#chart').clientWidth||1000)),sideWidth=225,columnGap=8,mainWidth=width-sideWidth-columnGap,height=340,left=58,right=8,top=8,bottom=28,plotRight=mainWidth-right;
  const levels=payload.lines?[payload.lines.cost,payload.lines.stop_loss,payload.lines.take_profit]:[],lows=series.map(p=>Number(p.low)),highs=series.map(p=>Number(p.high));let min=Math.min(...lows,...levels),max=Math.max(...highs,...levels);const padding=Math.max((max-min)*.05,Math.abs(max)*.003,.001);min-=padding;max+=padding;
  const plotWidth=plotRight-left,plotHeight=height-top-bottom,x=i=>left+(i+.5)*plotWidth/series.length,y=v=>top+(max-v)*plotHeight/(max-min||1),redUp=state.colorConvention==='redUp',upColor=redUp?'#ff5e6c':'#31d6a0',downColor=redUp?'#31d6a0':'#ff5e6c';
  let grid='';for(let i=0;i<8;i++){const gy=top+i*plotHeight/7,gv=max-i*(max-min)/7;grid+=`<line class="grid" x1="${left}" y1="${gy}" x2="${plotRight}" y2="${gy}"/><text class="y-axis-label" x="4" y="${gy+3}">${money(gv,max<10?3:2)}</text>`;}
  let plot='';if(payload.chart_type==='line'){const points=series.map((p,i)=>`${x(i)},${y(Number(p.close))}`).join(' ');plot=`<polygon class="area" points="${left},${height-bottom} ${points} ${plotRight},${height-bottom}"/><polyline class="price-line" points="${points}"/>`;}else{const candleWidth=Math.max(1,Math.min(9,plotWidth/series.length*.68));plot=series.map((p,i)=>{const open=Number(p.open),close=Number(p.close),color=close>=open?upColor:downColor,topY=Math.min(y(open),y(close)),body=Math.max(1,Math.abs(y(open)-y(close)));return `<line class="wick" stroke="${color}" x1="${x(i)}" y1="${y(Number(p.high))}" x2="${x(i)}" y2="${y(Number(p.low))}"/><rect class="candle" x="${x(i)-candleWidth/2}" y="${topY}" width="${candleWidth}" height="${body}" fill="${color}"/>`;}).join('');const ma=series.map((p,i)=>p.ma5==null?null:`${x(i)},${y(Number(p.ma5))}`).filter(Boolean).join(' ');if(ma)plot+=`<polyline class="ma-line" points="${ma}"/>`;}
  const levelMeta=payload.lines?[['成本',payload.lines.cost,'#68a9ff'],['止损',payload.lines.stop_loss,'#ff5e6c'],['止盈',payload.lines.take_profit,'#f5bc60']]:[],levelSvg=levelMeta.map(([name,value,color])=>`<line class="level" stroke="${color}" x1="${left}" y1="${y(value)}" x2="${plotRight}" y2="${y(value)}"/><text class="tag" fill="${color}" x="${left+5}" y="${y(value)-4}">${name} ${money(value,3)}</text>`).join('');
  const chips=buildVolumeProfile(series,min,max),leftStackHeight=599,chipFooterHeight=72,chipPlotHeight=leftStackHeight-chipFooterHeight,chipX=8,chipAxisWidth=46,chipAxisX=sideWidth-chipAxisWidth,chipMax=chipAxisX-chipX-4,chipY=price=>8+(max-price)*(chipPlotHeight-16)/(max-min||1),chipHeight=Math.max(.5,(chipPlotHeight-16)/chips.profile.length*.86),chipBars=chips.profile.map(item=>{const w=item.volume/chips.maxVolume*chipMax,color=item.price<=Number(series[series.length-1].close)?upColor:downColor;return `<rect class="chip-bar" x="${chipX}" y="${chipY(item.price)-chipHeight/2}" width="${w}" height="${chipHeight}" fill="${color}"/>`;}).join(''),chipAxisTicks=Array.from({length:8},(_,i)=>{const price=max-i*(max-min)/7,ty=chipY(price);return `<line class="chip-axis-grid" x1="${chipX}" y1="${ty}" x2="${chipAxisX}" y2="${ty}"/><line class="chip-axis-tick" x1="${chipAxisX-4}" y1="${ty}" x2="${chipAxisX+2}" y2="${ty}"/><text class="chip-axis-label" x="${chipAxisX+5}" y="${ty+3}">${money(price,max<10?3:2)}</text>`;}).join(''),chipHtml=`<aside class="chip-side-panel"><svg viewBox="0 0 ${sideWidth} ${chipPlotHeight}" preserveAspectRatio="none">${chipAxisTicks}${chipBars}<line class="chip-axis-line" x1="${chipAxisX}" y1="8" x2="${chipAxisX}" y2="${chipPlotHeight-8}"/></svg><div class="chip-footer"><strong>筹码分布（量价估算）</strong><span>70% ${money(chips.ranges.p70[0],3)}–${money(chips.ranges.p70[1],3)}　集中 ${chips.c70.toFixed(1)}%</span><span>90% ${money(chips.ranges.p90[0],3)}–${money(chips.ranges.p90[1],3)}　集中 ${chips.c90.toFixed(1)}%</span></div></aside>`;
  const labelAt=i=>{const t=String(series[i].time);return t.includes('T')?(state.period==='5d'?t.slice(5,10)+' '+t.slice(11,16):t.slice(11,16)):t.slice(2,10);},indices=[...new Set([0,Math.floor((series.length-1)/2),series.length-1])],labels=indices.map(i=>`<text x="${Math.max(left,x(i)-22)}" y="${height-8}">${safe(labelAt(i))}</text>`).join('');
  const indicatorScale={width:mainWidth,height:82,left,right,x};
  $('#chart').innerHTML=`<div id="chartHoverStats" class="chart-hover-stats"></div><div class="chart-body-grid"><div class="chart-left-stack"><div id="priceMainSurface" class="price-main-surface"><svg viewBox="0 0 ${mainWidth} ${height}" preserveAspectRatio="none"><defs><linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#67d9dc" stop-opacity=".22"/><stop offset="1" stop-color="#67d9dc" stop-opacity="0"/></linearGradient></defs>${grid}${plot}${levelSvg}${labels}<line id="crosshairX" class="crosshair" y1="${top}" y2="${height-bottom}" visibility="hidden"/><line id="crosshairY" class="crosshair" x1="${left}" x2="${plotRight}" visibility="hidden"/></svg></div><div class="indicator-stack">${indicatorSvg(series,indicatorScale,'amount',upColor,downColor)}${indicatorSvg(series,indicatorScale,'kdj',upColor,downColor)}${indicatorSvg(series,indicatorScale,'macd',upColor,downColor)}</div></div>${chipHtml}</div>`;
  hoverStats(series[series.length-1],windowState.end-1,payload);bindChartInteraction(payload,series,{width:mainWidth,height,left,right,top,bottom,plotRight,x,y});
  $('#volatility').textContent=`年化波动 ${(payload.risk.annualized_volatility*100).toFixed(1)}%`;$('#drawdown').textContent=`最大回撤 ${(payload.risk.max_drawdown*100).toFixed(1)}%`;$('#momentum').textContent=`20日动量 ${(payload.risk.momentum_20d*100).toFixed(1)}%`;const coverage=payload.coverage||{};const statusText=coverage.requirement_exempt?' · 固定五日窗口':(coverage.meets_required_history?' · 已达3年':' · 未达3年');const coverageText=coverage.first_bar?`${String(coverage.first_bar).slice(0,10)}—${String(coverage.last_bar).slice(0,10)}${statusText}`:'无本地数据';$('#chartFrequency').textContent=`${payload.period_label} · ${windowState.end-windowState.start}/${full.length} 条 · ${coverageText}`;$('#chartFrequency').className=coverage.requirement_exempt||coverage.meets_required_history?'coverage-ok':'coverage-gap';
}

function bindChartInteraction(payload,visible,scale){
  const element=$('#priceMainSurface'),windowState=state.chartWindow,full=payload.series;
  element.onwheel=event=>{event.preventDefault();const old=windowState.end-windowState.start,next=Math.max(12,Math.min(full.length,Math.round(old*(event.deltaY>0?1.18:.84)))),rect=element.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width*scale.width/scale.plotRight)),anchor=windowState.start+Math.round(old*ratio);windowState.start=Math.max(0,Math.min(full.length-next,anchor-Math.round(next*ratio)));windowState.end=windowState.start+next;renderChart(payload);};
  element.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();state.chartDrag={payload,startX:event.clientX,start:windowState.start,end:windowState.end,span:windowState.end-windowState.start,pixels:element.getBoundingClientRect().width,pendingX:event.clientX,frame:null};document.body.classList.add('chart-dragging');element.classList.add('dragging');};
  element.onpointerleave=()=>{if(!state.chartDrag)document.querySelectorAll('.crosshair').forEach(line=>line.setAttribute('visibility','hidden'));};
  element.onpointermove=event=>{if(state.chartDrag)return;const rect=element.getBoundingClientRect(),svgX=(event.clientX-rect.left)/rect.width*scale.width;if(svgX<scale.left||svgX>scale.plotRight)return;const index=Math.max(0,Math.min(visible.length-1,Math.floor((svgX-scale.left)/(scale.plotRight-scale.left)*visible.length))),point=visible[index],px=scale.x(index),py=scale.y(Number(point.close));hoverStats(point,windowState.start+index,payload);const crossX=$('#crosshairX'),crossY=$('#crosshairY');crossX.setAttribute('x1',px);crossX.setAttribute('x2',px);crossX.setAttribute('visibility','visible');crossY.setAttribute('y1',py);crossY.setAttribute('y2',py);crossY.setAttribute('visibility','visible');document.querySelectorAll('.linked-crosshair').forEach(line=>{line.setAttribute('x1',px);line.setAttribute('x2',px);line.setAttribute('visibility','visible');});};
}

document.addEventListener('pointermove',event=>{const drag=state.chartDrag;if(!drag)return;drag.pendingX=event.clientX;if(drag.frame)return;drag.frame=requestAnimationFrame(()=>{drag.frame=null;const delta=Math.round((drag.startX-drag.pendingX)/Math.max(drag.pixels,1)*drag.span),start=Math.max(0,Math.min(drag.payload.series.length-drag.span,drag.start+delta));if(start!==state.chartWindow.start){state.chartWindow.start=start;state.chartWindow.end=start+drag.span;renderChart(drag.payload);}});});
document.addEventListener('pointerup',()=>{if(!state.chartDrag)return;if(state.chartDrag.frame)cancelAnimationFrame(state.chartDrag.frame);state.chartDrag=null;document.body.classList.remove('chart-dragging');document.querySelectorAll('.price-main-surface').forEach(element=>element.classList.remove('dragging'));});
window.addEventListener('blur',()=>{state.chartDrag=null;document.body.classList.remove('chart-dragging');});

function renderOtherViews(data) {
  const sourceCards = data.source_health.map(s => `<div class="source-card"><span class="label">${safe(s.health_status)}</span><div><strong>${safe(s.name)}</strong><p>${safe(s.access_mode)} · ${safe(s.license_note)}${s.last_success_at ? ` · 最近成功 ${safe(s.last_success_at.slice(0,19))}` : ''}</p></div><span class="count">${s.enabled ? '启用' : '待配置'}</span></div>`).join('');
  $('#researchView').innerHTML = `${searchBar('research')}<div id="researchSearchResults"></div><section class="panel"><div class="panel-head"><div><p class="eyebrow">DATA SOURCE HEALTH</p><h2>免费数据源与信源审计</h2></div><span class="count">事件图 ${data.event_graph.graphs} 个</span></div>${sourceCards}${data.evidence.map(e => `<div class="source-card"><span class="label">（${safe(e.label)}）</span><div><strong>${safe(e.claim)}</strong><p>${safe(e.independent_check)}</p></div><span class="count">${safe(e.status)}</span></div>`).join('')}</section>`;
  renderStrategyLab(data.strategy_lab);
  renderReportLibraryShell();
  ['research','strategy','reports'].forEach(bindSearch);
}

function renderReportLibraryShell(){
  $('#reportsView').innerHTML=`${searchBar('reports')}<div id="reportsSearchResults"></div><section class="panel report-library"><div class="panel-head"><div><p class="eyebrow">REPORT WATCHER</p><h2>持仓主题、财报、公告与大盘研报</h2></div><div class="report-actions"><span id="reportWatcherState" class="count">每5分钟自动轮询</span><button id="refreshReportsButton" class="secondary-button">立即检查</button></div></div><p class="lab-warning">系统只归档公开资料并保留原文链接；券商研报评级属于外部观点，不等于事实，也不构成投资建议。</p><div id="reportLibraryList" class="report-list"><div class="search-loading">正在读取本地研报库…</div></div></section>`;
  $('#refreshReportsButton').onclick=async()=>{const button=$('#refreshReportsButton');button.disabled=true;button.textContent='后台检查中…';try{const result=await request('/api/reports/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});showToast(result.started?'已启动后台检查，页面会自动更新':'检查已在运行');window.setTimeout(loadReportLibrary,1500);}catch(error){showToast(`检查失败：${error.message}`);}finally{button.disabled=false;button.textContent='立即检查';}};
  loadReportLibrary();
}

async function loadReportLibrary(){try{const data=await request('/api/reports/library'),last=data.last_run,items=data.documents||[],refresh=data.refresh||{};$('#reportWatcherState').textContent=refresh.status==='RUNNING'?'正在后台检查…':last?`最近入库 ${safe(String(last.finished_at||'').slice(0,19))}`:'等待首次轮询';$('#reportLibraryList').innerHTML=items.length?items.map(item=>`<article class="report-item"><div><span class="badge">${safe(item.document_type)}</span><strong>${safe(item.title)}</strong><p>${safe(item.body)}</p><small>${safe(item.source_name)} · ${safe(item.published_at||item.captured_at||'时间未知')}</small></div>${item.source_url?`<a href="${safe(item.source_url)}" target="_blank" rel="noreferrer">查看原文 ↗</a>`:'<span class="count">索引记录</span>'}</article>`).join(''):'<div class="empty-state">尚未发现新的公开研报或公告；后台会继续每5分钟检查。</div>';}catch(error){$('#reportLibraryList').innerHTML=`<div class="empty-state">${safe(error.message)}</div>`;}}

function researchMetric(value,digits=2){return value===null||value===undefined||Number.isNaN(Number(value))?'—':Number(value).toFixed(digits);}

function miniCurveSvg(series,benchmark=[],width=420,height=132){
  if(!Array.isArray(series)||series.length<2)return '<div class="mini-chart-empty">运行后显示模拟净值曲线</div>';
  const all=[...series,...(Array.isArray(benchmark)?benchmark:[])].map(p=>Number(p.value)).filter(Number.isFinite),min=Math.min(...all),max=Math.max(...all),span=max-min||1,pad=9;
  const path=points=>points.map((point,index)=>`${index?'L':'M'} ${(pad+index/(points.length-1)*(width-pad*2)).toFixed(1)} ${(height-pad-(Number(point.value)-min)/span*(height-pad*2)).toFixed(1)}`).join(' ');
  const main=path(series),base=Array.isArray(benchmark)&&benchmark.length>1?`<path class="mini-benchmark" d="${path(benchmark)}"/>`:'';
  return `<svg class="mini-curve" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="模拟收益曲线"><defs><linearGradient id="miniFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#31d6a0" stop-opacity=".28"/><stop offset="1" stop-color="#31d6a0" stop-opacity="0"/></linearGradient></defs><path class="mini-area" d="${main} L ${width-pad} ${height-pad} L ${pad} ${height-pad} Z"/><path class="mini-main" d="${main}"/>${base}<text x="10" y="16">${safe(series[0].time)} · ${researchMetric(series[0].value)}</text><text x="${width-10}" y="16" text-anchor="end">${safe(series[series.length-1].time)} · ${researchMetric(series[series.length-1].value)}</text></svg>`;
}

function factorJudgement(item){const m=item.latest_metrics||{};if(m.ic===null||m.ic===undefined)return '尚未评估';const strength=Math.abs(Number(m.rank_ic||0));return strength>=.05?'在当前小样本中有一定截面区分度':strength>=.02?'区分度偏弱，需要扩大样本验证':'当前样本中几乎没有稳定区分度';}

function renderStrategyLab(lab) {
  const strategies=lab.strategies||[],factors=lab.factors||[],backtests=lab.backtests||[];
  const cards=strategies.map(item=>{const m=item.latest_metrics||{},curves=item.latest_curve||{};return `<article id="strategy-${safe(item.strategy_key)}" class="panel strategy-card ${state.strategyFocus===item.strategy_key?'result-focus':''}">
    <div class="panel-head"><div><span class="badge">${safe(item.category)} · ${safe(item.source_framework)}</span><h3>${safe(item.name)}</h3></div><span class="status-tag">${safe(item.status)}</span></div>
    <p>${safe(item.description)}</p>
    <div class="risk-box"><strong>该策略自己的风控</strong><div class="risk-metrics"><span>止损 <b>${(item.stop_loss_pct*100).toFixed(1)}%</b></span><span>止盈 <b>${(item.take_profit_pct*100).toFixed(1)}%</b></span><span>移动止损 <b>${(item.trailing_stop_pct*100).toFixed(1)}%</b></span><span>仓位上限 <b>${(item.max_position_pct*100).toFixed(0)}%</b></span><span>回撤红线 <b>${(item.max_drawdown_pct*100).toFixed(0)}%</b></span><span>单日损失 <b>${(item.max_daily_loss_pct*100).toFixed(1)}%</b></span></div><small>${safe(item.liquidity_rule)}</small></div>
    <div class="backtest-summary"><span>最近标的 <b>${safe(item.latest_symbol||'未运行')}</b></span><span>总收益 <b>${m.total_return_pct===undefined?'—':researchMetric(m.total_return_pct)+'%'}</b></span><span>最大回撤 <b>${m.max_drawdown_pct===undefined?'—':researchMetric(m.max_drawdown_pct)+'%'}</b></span><span>Sharpe <b>${researchMetric(m.sharpe_ratio)}</b></span></div>
    <div class="strategy-curve"><div class="curve-head"><strong>模拟盘净值</strong><span><i class="legend-main"></i>策略 <i class="legend-base"></i>买入持有</span></div>${miniCurveSvg(curves.strategy,curves.benchmark)}</div>
    <div id="strategy-result-${safe(item.strategy_key)}" class="inline-result">${m.total_return_pct===undefined?'选择标的后运行，结果会留在本卡片内。':`<strong>最近回测已完成</strong><span>${safe(item.latest_symbol)} · 收益 ${researchMetric(m.total_return_pct)}% · 年化 ${researchMetric(Number(m.annualized_return||0)*100)}% · 胜率 ${researchMetric(m.win_rate)}% · 交易 ${researchMetric(m.closed_trade_count,0)} 次</span>`}</div>
    <div class="research-actions"><select data-symbol-for="${safe(item.strategy_key)}"><option>510300</option><option>159915</option><option>600519</option><option>000001.SH</option></select><button class="primary-button run-backtest" data-strategy="${safe(item.strategy_key)}">运行三年+回测</button></div>
  </article>`;}).join('');
  const factorCards=factors.map(item=>{const m=item.latest_metrics||{};return `<article id="factor-${safe(item.factor_key)}" class="panel factor-card ${state.factorFocus===item.factor_key?'result-focus':''}"><div class="panel-head"><div><span class="badge">${safe(item.family)} · ${item.direction>0?'正向':'反向'}</span><h3>${safe(item.name)}</h3></div><button class="secondary-button run-factor" data-factor="${safe(item.factor_key)}">重新评估</button></div><code>${safe(item.expression)}</code><p>${safe(item.description)}</p><div class="factor-score"><span>IC <b>${researchMetric(item.ic,4)}</b></span><span>Rank IC <b>${researchMetric(item.rank_ic,4)}</b></span><span>模拟收益 <b>${m.simulated_return_pct===undefined?'—':researchMetric(m.simulated_return_pct)+'%'}</b></span><span>选中胜率 <b>${m.selected_win_rate_pct===undefined?'—':researchMetric(m.selected_win_rate_pct)+'%'}</b></span></div><div class="factor-curve"><div class="curve-head"><strong>因子选股模拟净值</strong><span>每日选排名第一，持有至下一交易日</span></div>${miniCurveSvg(item.equity_curve)}</div><div class="factor-pros-cons"><div><strong>优点</strong><ul>${(item.advantages||[]).map(text=>`<li>${safe(text)}</li>`).join('')}</ul></div><div><strong>局限</strong><ul>${(item.limitations||[]).map(text=>`<li>${safe(text)}</li>`).join('')}</ul></div></div><div id="factor-result-${safe(item.factor_key)}" class="inline-result"><strong>${safe(factorJudgement(item))}</strong><span>${safe(m.note||'点击评估后，这里显示结果。')}</span></div></article>`;}).join('');
  const runRows=backtests.map(item=>`<tr><td>#${item.id}</td><td>${safe(item.strategy)}</td><td>${safe(item.asset_symbol)}</td><td>${safe(item.data_start||'—')} → ${safe(item.data_end||'—')}</td><td>${safe(item.status)}</td><td>${item.metrics?researchMetric(item.metrics.total_return_pct)+'%':'—'}</td><td>${item.metrics?researchMetric(item.metrics.max_drawdown_pct)+'%':'—'}</td></tr>`).join('');
  $('#strategyView').innerHTML=`${searchBar('strategy')}<div id="strategySearchResults"></div>
    <section class="research-summary"><article class="metric-card"><span>版本化策略</span><strong>${strategies.length}</strong><small>每个策略绑定独立风控</small></article><article class="metric-card"><span>因子表达式</span><strong>${factors.length}</strong><small>AKQuant FactorEngine</small></article><article class="metric-card"><span>已保存回测</span><strong>${backtests.length}</strong><small>分析专用，不连接券商</small></article><article class="metric-card accent"><span>试验标的池</span><strong>${(lab.universe||[]).length}</strong><small>小样本结果不可直接交易</small></article></section>
    <section class="strategy-grid">${cards}</section>
    <section class="factor-lab"><div class="panel factor-lab-head"><div class="panel-head"><div><p class="eyebrow">FACTOR MINING</p><h2>因子挖掘、优缺点与模拟盘曲线</h2></div><span class="count">IC使用未来5日收益 · 曲线使用下一日收益</span></div><p class="lab-warning">当前只有 4 个标的，IC/Rank IC 与模拟曲线只用于验证管线，不具备统计显著性。模拟收益没有包含完整滑点、冲击成本和涨跌停无法成交约束。</p></div><div class="factor-grid">${factorCards}</div></section>
    <section class="panel research-table"><div class="panel-head"><div><p class="eyebrow">BACKTEST AUDIT</p><h2>回测审计记录</h2></div><span class="count">${safe((lab.frameworks||[]).join(' · '))}</span></div><div class="table-wrap"><table><thead><tr><th>编号</th><th>策略</th><th>标的</th><th>数据区间</th><th>状态</th><th>总收益</th><th>最大回撤</th></tr></thead><tbody>${runRows||'<tr><td colspan="7">尚未运行</td></tr>'}</tbody></table></div></section>`;
  bindStrategyLab();
}

function bindStrategyLab(){
  document.querySelectorAll('.run-backtest').forEach(button=>button.onclick=async()=>{const key=button.dataset.strategy,select=document.querySelector(`[data-symbol-for="${key}"]`),resultBox=$(`#strategy-result-${key}`);button.disabled=true;button.textContent='正在回测…';resultBox.className='inline-result running';resultBox.innerHTML='<strong>正在计算三年以上历史回测</strong><span>读取本地行情、执行策略专属风控并生成净值曲线…</span>';try{const result=await request('/api/research/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy_key:key,symbol:select.value})});state.strategyFocus=key;showToast(`回测完成：${result.symbol} ${researchMetric(result.metrics.total_return_pct)}%`);renderStrategyLab(await request('/api/strategy-lab'));bindSearch('strategy');requestAnimationFrame(()=>document.querySelector(`#strategy-${CSS.escape(key)}`)?.scrollIntoView({behavior:'smooth',block:'center'}));}catch(error){resultBox.className='inline-result failed';resultBox.innerHTML=`<strong>回测失败</strong><span>${safe(error.message)}</span>`;}finally{const live=document.querySelector(`#strategy-${CSS.escape(key)} .run-backtest`);if(live){live.disabled=false;live.textContent='运行三年+回测';}}});
  document.querySelectorAll('.run-factor').forEach(button=>button.onclick=async()=>{const key=button.dataset.factor,resultBox=$(`#factor-result-${key}`);button.disabled=true;button.textContent='计算中…';resultBox.className='inline-result running';resultBox.innerHTML='<strong>正在评估因子</strong><span>计算IC、Rank IC与模拟净值曲线…</span>';try{const result=await request('/api/research/factors/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({factor_key:key})});state.factorFocus=key;showToast(`因子完成：Rank IC ${researchMetric(result.metrics.rank_ic,4)}`);renderStrategyLab(await request('/api/strategy-lab'));bindSearch('strategy');requestAnimationFrame(()=>document.querySelector(`#factor-${CSS.escape(key)}`)?.scrollIntoView({behavior:'smooth',block:'center'}));}catch(error){resultBox.className='inline-result failed';resultBox.innerHTML=`<strong>因子评估失败</strong><span>${safe(error.message)}</span>`;}finally{const live=document.querySelector(`#factor-${CSS.escape(key)} .run-factor`);if(live){live.disabled=false;live.textContent='重新评估';}}});
}

function searchBar(page) {
  return `<section class="panel search-panel"><div><p class="eyebrow">LOCAL SEARCH</p><h2>页面搜索</h2></div><input id="${page}SearchInput" placeholder="输入代码、名称、事实、观点或概念"><select id="${page}SearchMode"><option value="exact">严格匹配</option><option value="semantic">语义匹配</option></select><button id="${page}SearchButton" class="primary-button">搜索</button></section>`;
}

function bindSearch(page) {
  const button=$(`#${page}SearchButton`),input=$(`#${page}SearchInput`);
  if(!button)return;
  const run=async()=>{ const q=input.value.trim();if(!q)return; const mode=$(`#${page}SearchMode`).value,target=$(`#${page}SearchResults`);target.innerHTML='<div class="search-loading">正在检索…</div>';try{const data=await request(`/api/search?q=${encodeURIComponent(q)}&mode=${mode}&page=${page}`);target.innerHTML=data.results.length?`<section class="panel search-results">${data.results.map(r=>`<article><span>${safe(r.match_mode)} · ${Number(r.score).toFixed(3)}</span><strong>${safe(r.title)}</strong><p>${safe(r.snippet)}</p><small>${safe(r.source_ref||'本地记录')}</small></article>`).join('')}</section>`:'<div class="empty-state">没有匹配结果</div>';}catch(error){target.innerHTML=`<div class="empty-state">${safe(error.message)}。严格匹配仍可使用；语义模型可按文档安装。</div>`;}};
  button.onclick=run;input.onkeydown=event=>{if(event.key==='Enter')run();};
}

async function renderGraphPage() {
  const listing=await request('/api/event-graphs');
  $('#graphsView').innerHTML=`${searchBar('graphs')}<div id="graphsSearchResults"></div><section class="panel graph-panel"><div class="panel-head"><div><p class="eyebrow">EVENT GRAPH OBSERVER</p><h2>事件图选择与观测</h2></div><select id="graphSelect">${listing.graphs.map(g=>`<option value="${g.id}">${safe(g.title)}</option>`).join('')}</select></div><div class="graph-workspace"><div id="graphCanvas" class="graph-canvas"></div><aside id="graphInspector" class="graph-inspector">选择节点查看属性</aside></div></section>`;
  bindSearch('graphs');
  const load=async id=>{const data=await request(`/api/event-graphs/${id}`);state.graph=data;renderGraph(data);};
  $('#graphSelect').onchange=event=>load(event.target.value);
  if(listing.graphs.length)await load(listing.graphs[0].id);else $('#graphCanvas').innerHTML='<div class="empty-state">暂无事件图</div>';
}

function renderGraph(data) {
  const width=900,height=500,cx=450,cy=250,r=Math.min(180,55+data.nodes.length*12),positions={};
  data.nodes.forEach((node,i)=>{const a=data.nodes.length===1?0:i*Math.PI*2/data.nodes.length;positions[node.id]={x:cx+Math.cos(a)*r,y:cy+Math.sin(a)*r};});
  const edges=data.edges.map(edge=>{const a=positions[edge.from_node_id],b=positions[edge.to_node_id];return a&&b?`<line class="graph-edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/><text class="edge-label" x="${(a.x+b.x)/2}" y="${(a.y+b.y)/2}">${safe(edge.relation_type)}</text>`:'';}).join('');
  const nodes=data.nodes.map(node=>{const p=positions[node.id];return `<g class="graph-node" data-id="${node.id}" transform="translate(${p.x},${p.y})"><circle r="34"/><text y="-3">${safe(node.node_type.slice(0,12))}</text><text y="13">${safe(node.label.slice(0,10))}</text></g>`;}).join('');
  $('#graphCanvas').innerHTML=`<svg viewBox="0 0 ${width} ${height}">${edges}${nodes}</svg>`;
  document.querySelectorAll('.graph-node').forEach(el=>el.onclick=()=>{const node=data.nodes.find(n=>n.id===Number(el.dataset.id));$('#graphInspector').innerHTML=`<h3>${safe(node.label)}</h3><p>（${safe(node.fact_opinion)}）</p><dl><dt>类型</dt><dd>${safe(node.node_type)}</dd><dt>观测时间</dt><dd>${safe(node.observed_at||'未知')}</dd><dt>关系库引用</dt><dd>${safe(node.relational_ref||'无')}</dd><dt>属性</dt><dd>${safe(node.properties_json)}</dd></dl>`;});
}

async function renderChatPage() {
  const [data,modelData]=await Promise.all([request('/api/chat/sessions'),request('/api/chat/models')]);
  const options=modelData.models.map(model=>`<option value="${safe(model.id)}" ${model.id===modelData.default_model?'selected':''}>${safe(model.label)} · ${safe(model.provider)}</option>`).join('');
  $('#chatView').innerHTML=`<section class="chat-layout panel"><aside class="chat-sessions"><button id="newChatButton">＋ 新对话</button><div id="chatSessionList">${data.sessions.map(s=>`<button data-session="${s.id}">${safe(s.title)}</button>`).join('')}</div></aside><div class="chat-main"><div class="chat-agent-toolbar"><label>Agent 模型<select id="chatModelSelect" aria-label="选择 Agent LLM">${options}</select></label><small>每条回复保存实际模型名；切换只影响下一条消息</small></div><div id="chatMessages" class="chat-messages"><div class="chat-welcome"><strong>RoofTop Research Agent</strong><p>我会区分事实、观点和待验证假设，只做投资研究，不执行交易。</p></div></div><div class="chat-compose"><textarea id="chatInput" rows="3" placeholder="向研究 Agent 提问…"></textarea><button id="sendChatButton" class="primary-button">发送</button></div></div></section>`;
  $('#newChatButton').onclick=()=>{state.chatSession=null;$('#chatMessages').innerHTML='<div class="chat-welcome"><strong>新对话</strong><p>请输入研究问题。</p></div>';};
  document.querySelectorAll('#chatSessionList button').forEach(button=>button.onclick=()=>loadChat(Number(button.dataset.session)));
  $('#sendChatButton').onclick=sendChat;
  $('#chatInput').onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey))sendChat();};
}

async function loadChat(sessionId){state.chatSession=sessionId;const data=await request(`/api/chat/messages?session_id=${sessionId}`);$('#chatMessages').innerHTML=data.messages.map(m=>`<article class="chat-message ${m.role}"><span>${m.role==='user'?'你':'Agent'}</span><p>${safe(m.content)}</p><small>${safe(m.model_name||'')}</small></article>`).join('');const lastModel=[...data.messages].reverse().find(message=>message.role==='assistant'&&message.model_name)?.model_name,selector=$('#chatModelSelect');if(lastModel&&selector&&[...selector.options].some(option=>option.value===lastModel))selector.value=lastModel;$('#chatMessages').scrollTop=$('#chatMessages').scrollHeight;}

async function sendChat(){const input=$('#chatInput'),message=input.value.trim(),model=$('#chatModelSelect')?.value;if(!message)return;input.value='';const box=$('#chatMessages');box.insertAdjacentHTML('beforeend',`<article class="chat-message user"><span>你</span><p>${safe(message)}</p></article><article id="chatThinking" class="chat-message assistant"><span>Agent · ${safe(model)}</span><p>正在分析…</p></article>`);try{const data=await request('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:state.chatSession,message,model})});state.chatSession=data.session_id;$('#chatThinking').remove();box.insertAdjacentHTML('beforeend',`<article class="chat-message assistant"><span>Agent</span><p>${safe(data.message.content)}</p><small>${safe(data.message.model_name)}</small></article>`);}catch(error){$('#chatThinking').innerHTML=`<span>系统</span><p>${safe(error.message)}</p>`;}box.scrollTop=box.scrollHeight;}

function bindNavigation() {
  const titles = {overview:'市场与持仓总览',research:'情报研究',strategy:'策略与风控',reports:'研报与策略库',graphs:'事件图观测',chat:'Agent Chat'};
  document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
    button.classList.add('active');
    $(`#${button.dataset.view}View`).classList.add('active');
    $('#pageTitle').textContent = titles[button.dataset.view];
    if(button.dataset.view==='strategy'&&!document.querySelector('#strategyView .strategy-card')&&state.dashboard)renderStrategyLab(state.dashboard.strategy_lab);
    if(button.dataset.view==='reports'&&!document.querySelector('#reportsView .report-library')){renderReportLibraryShell();bindSearch('reports');}
  }));
  const requested=new URLSearchParams(location.search).get('view');
  const requestedButton=document.querySelector(`.nav-item[data-view="${requested}"]`);
  if(requestedButton) requestedButton.click();
}

async function initialize() {
  try {
    const selectedSymbol = state.selected && state.selected.symbol;
    const data = normalizeDashboard(await request('/api/dashboard'));
    state.dashboard = data;
    $('#warning').textContent = `重要：${data.meta.warning}`;
    $('#asOf').textContent = data.meta.as_of;
    $('#dataMode').textContent = data.meta.mode === 'FREE_DELAYED_REALTIME' ? '● FREE / 分钟行情' : '● LOCAL / 日频';
    renderMarkets(data.markets);
    renderSummary(data.portfolio);
    renderPositions(data.portfolio.positions);
    renderHypotheses(data.hypotheses);
    if(!$('#researchView').children.length&&!$('#strategyView').children.length&&!$('#reportsView').children.length)renderOtherViews(data);
    const assets=[...data.portfolio.positions,...data.markets.filter(m=>!data.portfolio.positions.some(p=>p.symbol===m.symbol))];
    $('#assetSelect').innerHTML = assets.map(p => `<option value="${safe(p.symbol)}">${safe(p.name)} · ${safe(p.symbol)}</option>`).join('');
    if (!assets.length) throw new Error('本地数据库中没有可展示的持仓或行情标的');
    const nextSymbol = assets.some(p => p.symbol === selectedSymbol) ? selectedSymbol : assets[0].symbol;
    $('#assetSelect').value = nextSymbol;
    await selectAsset(nextSymbol);
    if (state.chart.legacy_backend) {
      $('#graphsView').innerHTML = '<div class="empty-state">旧版后台不包含事件图观测 API，请重启后台。</div>';
      $('#chatView').innerHTML = '<div class="empty-state">旧版后台不包含 Agent Chat API，请重启后台。</div>';
    } else {
      if(!$('#graphsView').children.length) await renderGraphPage();
      if(!$('#chatView').children.length) await renderChatPage();
    }
  } catch (error) {
    $('#warning').textContent = `载入失败：${error.message}`;
    $('#warning').classList.add('negative');
  }
}

$('#assetSelect').addEventListener('change', event => selectAsset(event.target.value));
$('#colorConvention').value=state.colorConvention;
$('#colorConvention').addEventListener('change',event=>{state.colorConvention=event.target.value;localStorage.setItem('rooftop-color-convention',state.colorConvention);if(state.chart){renderChart(state.chart);const change=state.chart.quote?state.chart.quote.change_pct:state.selected?.pnl_pct;$('#assetPnl').style.color=chartTrendColor(change||0);}});
$('#recheckButton').addEventListener('click', async () => {
  const p = state.selected;
  if (!p) return;
  const result = await request('/api/risk/evaluate', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  showToast(`纪律检查完成：${actionLabel(result.discipline.action)}`);
});
$('#methodButton').addEventListener('click', () => showToast('纪律优先：事实与观点分离，触线必复核，所有猜测均留反证条件。'));
bindNavigation();
initialize();
window.setInterval(refreshRealtimeOverview, 5_000);
window.setInterval(()=>{if(document.querySelector('#reportsView.active'))loadReportLibrary();},60_000);
