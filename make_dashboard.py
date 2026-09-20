"""weekly_fx.json + weekly_backfill.json + weekly_history.json -> 정적 HTML 대시보드"""
import json, os, argparse
from datetime import datetime

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="report_data/dashboard/index.html")
args = ap.parse_args()

fxd = json.load(open("report_data/weekly_fx.json"))
bf = json.load(open("report_data/weekly_backfill.json"))
snap = bf.get("today_snapshot", {})
S = fxd["summary"]
weeks = fxd["weekly"]
last = weeks[-1]

fx_now = snap.get("fx_rate", last["fx"])
total_usd = S["total_usd_corrected"]
total_krw = S["total_krw_corrected"]
p_krw, p_usd = last["principal_krw"], last["principal_usd"]
tr_sum = sum(w["weekly_trading_krw"] for w in weeks)
rp_sum = sum(w["weekly_rp_krw"] for w in weeks)
fx_sum = sum(w["weekly_fx_krw"] for w in weeks)
pnl_krw = total_krw - p_krw
pnl_usd = total_usd - p_usd

try:
    hist = json.load(open("report_data/weekly_history.json"))
except Exception:
    hist = []
last_h = hist[-1] if isinstance(hist, list) and hist else {}
rp_acc = S["rp_net_by_account"]
accs = []
for a in last_h.get("accounts", []):
    k = rp_acc.get(a["id"], 0.0)
    b = dict(a)
    b["rp_balance_usd"] = a["rp_balance_usd"] + k
    b["total_usd"] = a["total_usd"] + k
    b["rp_net_usd"] = k
    accs.append(b)
tr = sum(a["trading_pnl_usd"] for a in accs)
dv = sum(a["dividend_net_usd"] for a in accs)
rp_net = S["rp_net_total_usd"]

data = dict(
    generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    asof=last["week_end"],
    hist_date=last_h.get("date", ""),
    kpi=dict(total_krw=total_krw, principal_krw=p_krw, pnl_krw=pnl_krw,
             pnl_pct=pnl_krw / p_krw * 100,
             total_usd=total_usd, principal_usd=p_usd, pnl_usd=pnl_usd,
             pnl_usd_pct=pnl_usd / p_usd * 100,
             trading_krw=tr_sum, rp_krw=rp_sum, rp_usd=rp_net, fx_krw=fx_sum,
             other_krw=pnl_krw - tr_sum - rp_sum - fx_sum,
             fx_now=fx_now, avg_buy_fx=p_krw / p_usd),
    comp=dict(cash=snap.get("cash_usd", 0), rp=snap.get("rp_balance_usd", 0) + rp_net,
              stock=snap.get("stock_eval_usd", 0)),
    accounts=accs,
    usd_breakdown=dict(trading=tr, dividend=dv, rp_net=rp_net,
                       resid=pnl_usd - tr - dv - rp_net, total=pnl_usd, fees=S["fees_usd"]),
    weeks=weeks,
)

HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>JYN 결산 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#f6f7f9;--card:#fff;--line:#e5e7eb;--text:#111827;--mute:#6b7280;--up:#dc2626;--down:#2563eb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Malgun Gothic","Apple SD Gothic Neo",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mute);font-size:13px;margin-bottom:18px}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.k-l{font-size:12px;color:var(--mute)}.k-v{font-size:21px;font-weight:700;margin-top:4px}
.k-s{font-size:12px;color:var(--mute);margin-top:2px}
.pos{color:var(--up)}.neg{color:var(--down)}.mute{color:var(--mute);font-size:11px}
.charts{grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
@media(max-width:520px){.charts{grid-template-columns:1fr}}
.card h2{font-size:14px;margin:0 0 10px}
.cv{position:relative;height:270px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--mute);font-weight:600}
tr.tot td{font-weight:700;border-top:2px solid var(--line)}
.tw{overflow-x:auto}.note{font-size:12px;color:var(--mute);line-height:1.6;margin-top:14px}
</style></head><body><div class="wrap">
<h1>제이와이앤파트너스 · SOXL 결산</h1>
<div class="sub" id="sub"></div>
<div class="grid kpis" id="kpis"></div>
<div class="grid charts">
 <div class="card"><h2>총자산 vs 투자원금 (백만원)</h2><div class="cv"><canvas id="c1"></canvas></div></div>
 <div class="card"><h2>주간 손익 분해: 매매·배당 / RP이자 / 환차손익 (백만원)</h2><div class="cv"><canvas id="c2"></canvas></div></div>
 <div class="card"><h2>누적 손익 분해 (백만원)</h2><div class="cv"><canvas id="c3"></canvas></div></div>
 <div class="card"><h2>적용환율 (원/USD)</h2><div class="cv"><canvas id="c4"></canvas></div></div>
</div>
<div class="grid charts" style="margin-top:12px">
 <div class="card"><h2>자산 구성 (USD)</h2><div class="cv"><canvas id="c5"></canvas></div></div>
 <div class="card"><h2>USD 손익 구성</h2><div class="tw"><table id="bd"></table></div><div class="note" id="bdnote"></div></div>
</div>
<div class="card" style="margin-top:12px" id="acctcard"><h2>계좌별 상세</h2><div class="tw"><table id="acct"></table></div></div>
<div class="card" style="margin-top:12px"><h2>주별 내역 (백만원)</h2><div class="tw"><table id="tbl"></table></div></div>
<div class="note" id="note"></div>
</div>
<script>
const D=__DATA__;
const M=v=>v/1e6, f1=v=>M(v).toLocaleString('ko-KR',{minimumFractionDigits:1,maximumFractionDigits:1});
const sgn=v=>v>0?'pos':(v<0?'neg':'');
const won=v=>Math.round(v).toLocaleString('ko-KR');
const usd=v=>(v<0?'-':'')+'$'+Math.abs(Math.round(v)).toLocaleString('en-US');
const usd2=v=>(v<0?'-':'')+'$'+Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const K=D.kpi, W=D.weeks, L=W.map(w=>w.week_end.slice(5));
document.getElementById('sub').textContent='기준일 '+D.asof+' · 생성 '+D.generated_at+(D.hist_date?' · 계좌별 기준 '+D.hist_date:'')+' · 원금 = 환전에 사용된 원화 기준';
const cards=[
 ['총자산',won(K.total_krw)+'원','$'+K.total_usd.toLocaleString('en-US',{maximumFractionDigits:0})+' (RP이자 보정 반영)',''],
 ['투자원금',won(K.principal_krw)+'원','평균 환전환율 '+K.avg_buy_fx.toFixed(1),''],
 ['원화 손익',(K.pnl_krw>0?'+':'')+won(K.pnl_krw)+'원',(K.pnl_pct>0?'+':'')+K.pnl_pct.toFixed(2)+'% (원금 대비)',sgn(K.pnl_krw)],
 ['매매·배당 누적',(K.trading_krw>0?'+':'')+won(K.trading_krw)+'원','수수료·제세금 차감 후, 발생시점 환율',sgn(K.trading_krw)],
 ['RP이자 누적(세후)',(K.rp_krw>0?'+':'')+won(K.rp_krw)+'원',usd(K.rp_usd)+' · 발생시점 환율',sgn(K.rp_krw)],
 ['환차손익 누적',(K.fx_krw>0?'+':'')+won(K.fx_krw)+'원','현재 환율 '+K.fx_now.toFixed(1),sgn(K.fx_krw)],
 ['USD 기준 손익',(K.pnl_usd>0?'+':'')+usd(K.pnl_usd),(K.pnl_usd_pct>0?'+':'')+K.pnl_usd_pct.toFixed(2)+'% (환율 제외)',sgn(K.pnl_usd)]
];
document.getElementById('kpis').innerHTML=cards.map(c=>`<div class="card"><div class="k-l">${c[0]}</div><div class="k-v ${c[3]}">${c[1]}</div><div class="k-s">${c[2]}</div></div>`).join('');
Chart.defaults.font.family='-apple-system,"Malgun Gothic",sans-serif';Chart.defaults.font.size=11;
const base={responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
 plugins:{legend:{position:'bottom',labels:{boxWidth:12}}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:10}}}};
const ln=(label,data,color,extra={})=>Object.assign({label,data,borderColor:color,backgroundColor:color,borderWidth:2,pointRadius:0,tension:.15},extra);
new Chart(c1,{type:'line',data:{labels:L,datasets:[ln('총자산(재구성)',W.map(w=>M(w.equity_krw)),'#0f766e'),ln('투자원금',W.map(w=>M(w.principal_krw)),'#4b5563',{stepped:true,borderDash:[5,4]})]},options:base});
new Chart(c2,{type:'bar',data:{labels:L,datasets:[
 {label:'매매·배당',data:W.map(w=>M(w.weekly_trading_krw)),backgroundColor:'#0f766e'},
 {label:'RP이자',data:W.map(w=>M(w.weekly_rp_krw)),backgroundColor:'#7c3aed'},
 {label:'환차손익',data:W.map(w=>M(w.weekly_fx_krw)),backgroundColor:'#d97706'}]},
 options:Object.assign({},base,{scales:{x:{stacked:true,grid:{display:false},ticks:{maxTicksLimit:10}},y:{stacked:true}}})});
let ct1=0,cr1=0,cf1=0;const CT=[],CR=[],CF=[];
W.forEach(w=>{ct1+=w.weekly_trading_krw;cr1+=w.weekly_rp_krw;cf1+=w.weekly_fx_krw;CT.push(M(ct1));CR.push(M(cr1));CF.push(M(cf1));});
new Chart(c3,{type:'line',data:{labels:L,datasets:[ln('누적 매매·배당',CT,'#0f766e'),ln('누적 RP이자',CR,'#7c3aed'),ln('누적 환차손익',CF,'#d97706'),
 ln('합계(원화손익)',CT.map((v,i)=>v+CR[i]+CF[i]),'#111827',{borderWidth:2.5})]},options:base});
new Chart(c4,{type:'line',data:{labels:L,datasets:[ln('적용환율',W.map(w=>w.fx),'#7c3aed'),ln('평균 환전환율',W.map(()=>K.avg_buy_fx),'#9ca3af',{borderDash:[5,4],borderWidth:1.5})]},options:base});
const C=D.comp, ct=C.cash+C.rp+C.stock;
new Chart(c5,{type:'doughnut',data:{labels:['현금','RP','보유주식'],datasets:[{data:[C.cash,C.rp,C.stock],backgroundColor:['#0f766e','#7c3aed','#d97706'],borderColor:'#fff',borderWidth:2}]},
 options:{responsive:true,maintainAspectRatio:false,cutout:'58%',plugins:{legend:{position:'bottom',labels:{boxWidth:12}},
 tooltip:{callbacks:{label:c=>' '+c.label+' '+usd(c.parsed)+' ('+(c.parsed/ct*100).toFixed(1)+'%)'}}}}});
const B=D.usd_breakdown;
const brow=(n,v,s)=>`<tr><td>${n}${s?' <span class="mute">'+s+'</span>':''}</td><td class="${sgn(v)}">${usd2(v)}</td></tr>`;
document.getElementById('bd').innerHTML=
 brow('매매손익',B.trading,'수수료·제세금 차감 후')+brow('배당 (세후)',B.dividend,'')+brow('RP이자 (세후)',B.rp_net,'환매도 세금으로 역산')+
 brow('기타·잔차',B.resid,'검산용')+`<tr class="tot"><td>USD 손익 합계</td><td class="${sgn(B.total)}">${usd2(B.total)}</td></tr>`;
document.getElementById('bdnote').textContent='합계 = 보정 총자산 − 환전 투자원금(USD). 참고: 수수료·제세금 누계 약 '+usd(B.fees)+'는 매매손익에 이미 반영되어 있습니다. RP이자는 환매도 세금(15.4%)에서 역산한 추정치입니다.';
const A=D.accounts||[];
if(A.length){
 const Sm=k=>A.reduce((s,a)=>s+a[k],0);
 const r=a=>`<tr><td>${a.id} <span class="mute">${a.label}</span></td><td>${usd(a.total_usd)}</td><td>${usd(a.cash_usd)}</td><td>${usd(a.rp_balance_usd)}</td><td>${usd(a.stock_eval_usd)}</td>
 <td class="${sgn(a.trading_pnl_usd)}">${usd(a.trading_pnl_usd)}</td><td>${usd2(a.dividend_net_usd)}</td><td class="${sgn(a.rp_net_usd)}">${usd(a.rp_net_usd)}</td><td>${won(a.rp_tax_krw)}</td></tr>`;
 document.getElementById('acct').innerHTML='<tr><th>계좌</th><th>총자산</th><th>현금</th><th>RP(보정)</th><th>주식</th><th>매매손익</th><th>배당(세후)</th><th>RP이자(세후)</th><th>RP세금(원)</th></tr>'+A.map(r).join('')+
 `<tr class="tot"><td>합계</td><td>${usd(Sm('total_usd'))}</td><td>${usd(Sm('cash_usd'))}</td><td>${usd(Sm('rp_balance_usd'))}</td><td>${usd(Sm('stock_eval_usd'))}</td><td class="${sgn(Sm('trading_pnl_usd'))}">${usd(Sm('trading_pnl_usd'))}</td><td>${usd2(Sm('dividend_net_usd'))}</td><td class="${sgn(Sm('rp_net_usd'))}">${usd(Sm('rp_net_usd'))}</td><td>${won(Sm('rp_tax_krw'))}</td></tr>`;
}else{document.getElementById('acctcard').style.display='none';}
const hd='<tr><th>주(일)</th><th>환율</th><th>원금</th><th>총자산</th><th>원화손익</th><th>주간 매매·배당</th><th>주간 RP</th><th>주간 환차</th><th>주간 순입금</th></tr>';
const rows=[...W].reverse().map(w=>`<tr><td>${w.week_end}</td><td>${w.fx.toFixed(1)}</td><td>${f1(w.principal_krw)}</td><td>${f1(w.equity_krw)}</td>
<td class="${sgn(w.pnl_krw)}">${f1(w.pnl_krw)}</td><td class="${sgn(w.weekly_trading_krw)}">${f1(w.weekly_trading_krw)}</td>
<td class="${sgn(w.weekly_rp_krw)}">${f1(w.weekly_rp_krw)}</td><td class="${sgn(w.weekly_fx_krw)}">${f1(w.weekly_fx_krw)}</td><td>${f1(w.weekly_net_in_krw)}</td></tr>`).join('');
document.getElementById('tbl').innerHTML=hd+rows;
document.getElementById('note').innerHTML='· 주별 총자산은 실현손익 기준 재구성값(미실현손익 제외)입니다. 상단 총자산은 오늘 API 잔고에 RP이자 보정을 더한 값이고, 두 값의 차이 때문에 합계와 원화손익이 '+won(K.other_krw)+'원 다릅니다.<br>· 환율은 4계좌의 거래일 적용환율 평균이고, 매매·RP 손익은 그 주 환율로, 환차손익은 (전주 자산×환율변동 + 신규 환전분 환율변동)으로 계산했습니다.<br>· 원금은 원화→USD 환전(외화매수)에 사용된 원화 누적이며, 계좌간 외화이체는 합산 시 상쇄됩니다. 색상은 손익 양(+)이 빨강, 음(−)이 파랑입니다.';
</script></body></html>"""

html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
open(args.out, "w", encoding="utf-8").write(html)
print("생성:", args.out, f"({len(html):,} bytes)")
