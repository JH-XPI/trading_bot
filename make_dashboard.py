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
    b["rp_balance_usd"] = a["rp_balance_usd"]
    b["total_usd"] = a["total_usd"]
    b["rp_net_usd"] = k
    accs.append(b)
tr = sum(a["trading_pnl_usd"] for a in accs)
dv = sum(a["dividend_net_usd"] for a in accs)
rp_net = S["rp_net_total_usd"]

import subprocess
_FILES = ["make_dashboard.py", "weekly_fx.py", "run_dashboard.sh"]
def _git_version():
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        h = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=d,
                                    stderr=subprocess.DEVNULL, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--"] + _FILES, cwd=d,
                                        stderr=subprocess.DEVNULL, text=True).strip()
        return h + ("*" if dirty else "")
    except Exception:
        return ""

data = dict(
    generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    ts=datetime.now().timestamp(),
    version="v7",
    commit=_git_version(),
    asof=last["week_end"],
    hist_date=last_h.get("date", ""),
    kpi=dict(total_krw=total_krw, principal_krw=p_krw, pnl_krw=pnl_krw,
             pnl_pct=pnl_krw / p_krw * 100,
             total_usd=total_usd, principal_usd=p_usd, pnl_usd=pnl_usd,
             pnl_usd_pct=pnl_usd / p_usd * 100,
             trading_krw=tr_sum, rp_krw=rp_sum, rp_usd=rp_net, fx_krw=fx_sum,
             other_krw=pnl_krw - tr_sum - rp_sum - fx_sum,
             fx_now=fx_now, avg_buy_fx=p_krw / p_usd),
    comp=dict(cash=snap.get("cash_usd", 0), rp=snap.get("rp_balance_usd", 0),
              stock=snap.get("stock_eval_usd", 0)),
    accounts=accs,
    usd_breakdown=dict(trading=tr, dividend=dv, rp_net=rp_net,
                       resid=pnl_usd - tr - dv - rp_net, total=pnl_usd, fees=S["fees_usd"]),
    weeks=weeks,
)

_first = datetime.strptime(weeks[0]["week_end"], "%Y-%m-%d")
_lastd = datetime.strptime(weeks[-1]["week_end"], "%Y-%m-%d")
_months = max(((_lastd - _first).days + 7) / 30.4375, 1)
_avgcap = sum(w["principal_krw"] for w in weeks) / len(weeks)
data["calc"] = dict(invest_krw=tr_sum + rp_sum, hist_monthly=(tr_sum + rp_sum) / _avgcap / _months,
                    hist_months=_months)
data["book_krw"] = p_krw + tr_sum + rp_sum   # 장부 원화 = 원금 + 발생시점 환율 손익
try:
    data["yearend"] = json.load(open("report_data/yearend_fx.json"))
except Exception:
    data["yearend"] = None

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
<div id="status" style="display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px;font-size:13px;margin-bottom:12px"></div>
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
<div class="grid charts" style="margin-top:12px">
 <div class="card"><h2>연말 환율 시나리오 (외화평가손익, 백만원)</h2><div class="tw"><table id="ye"></table></div><div class="note" id="yenote"></div></div>
 <div class="card"><h2>세금·비용 요약</h2><div class="tw"><table id="tx"></table></div><div class="note" id="txnote"></div></div>
</div>
<style>#taxin input{border:1px solid #d1d5db;border-radius:6px;padding:4px 6px;font-size:13px;background:#fff;color:#111827}#taxin label{display:flex;flex-direction:column;gap:3px;color:#6b7280;font-size:12px}</style>
<div class="card" style="margin-top:12px" id="taxcard">
 <h2>법인세 예상 계산기 (가정 기반)</h2>
 <div id="taxin" style="display:flex;flex-wrap:wrap;gap:10px 18px;margin-bottom:12px">
  <label>연말까지 총 비용 (원)<input id="t_cost" type="number" step="1000000" style="width:140px"></label>
  <label>연말 환율<input id="t_fx" type="number" step="1" style="width:90px"></label>
  <label>남은 개월<input id="t_m" type="number" step="0.1" style="width:70px"></label>
  <label>과세표준 가감 (원, +가산 / −공제)<input id="t_adj" type="number" step="1000000" style="width:160px"></label>
 </div>
 <div class="tw"><table id="taxtbl"></table></div>
 <div class="note" id="taxnote"></div>
</div>
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
document.getElementById('sub').textContent='원금 = 환전에 사용된 원화 기준 · 적용환율 '+K.fx_now.toFixed(1)+' · 손익 분석 기간 5/4~'+D.asof.slice(5);
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
// ---- v4: 지연 경고 / 연말 환율 시나리오 / 세금·비용 ----
(function(){
 const el=document.getElementById('status');
 const age=(Date.now()/1000-D.ts)/3600, late=age>36;
 const kst=new Date(D.ts*1000+9*3600*1000);
 const p2=n=>String(n).padStart(2,'0');
 const gen=kst.getUTCFullYear()+'-'+p2(kst.getUTCMonth()+1)+'-'+p2(kst.getUTCDate())+' '+p2(kst.getUTCHours())+':'+p2(kst.getUTCMinutes())+' KST';
 const dot='<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:'+(late?'#dc2626':'#16a34a')+';margin-right:6px"></span>';
 const ver=D.version+(D.commit?' (커밋 '+D.commit+')':'');
 if(late){el.style.borderColor='#fecaca';el.style.background='#fef2f2';}
 el.innerHTML=dot+'<b style="color:'+(late?'#b91c1c':'inherit')+'">'+(late?'갱신 지연 · ':'')+'데이터 기준 '+gen+'</b>'
  +(late?'<span style="color:#b91c1c">약 '+Math.floor(age)+'시간 경과 - 자동 갱신(cron) 상태를 확인하세요</span>':'')
  +'<span class="mute">계좌별 기준 '+(D.hist_date||'-')+'</span>'
  +'<span class="mute">화면 버전 '+ver+'</span>'
  +'<span class="mute">매일 07:00(KST) 자동 갱신</span>';
})();
const UA=K.total_usd, BK=D.book_krw, BE=BK/UA;
const mkY=(label,x,bold)=>{const fxp=UA*x-BK, tot=UA*x-K.principal_krw;
 return `<tr${bold?' class="tot"':''}><td>${label}</td><td>${x.toFixed(1)}</td><td class="${sgn(fxp)}">${f1(fxp)}</td><td class="${sgn(tot)}">${f1(tot)}</td></tr>`;};
let yeHtml='<tr><th>구분</th><th>환율</th><th>외화평가손익</th><th>누적 총손익</th></tr>'+mkY('현재',K.fx_now,true);
[1300,1350,1400,1450,1500].forEach(x=>{yeHtml+=mkY('가정',x,false);});
yeHtml+=mkY('평가손익 0 (장부환율)',BE,false);
if(D.yearend&&D.yearend.rate){yeHtml+=mkY('연말 확정 '+(D.yearend.date||''),D.yearend.rate,true);}
document.getElementById('ye').innerHTML=yeHtml;
document.getElementById('yenote').textContent='현재 외화자산 '+usd(UA)+'을 그대로 연말까지 보유한다고 가정한 값입니다(이후 손익·입금 미반영). 장부환율은 원금과 발생시점 환율 손익을 합한 원화 장부가 ÷ 외화자산이며, 환율 10원 변동 ≈ '+f1(UA*10)+'백만원입니다. 연말 평가는 고시 매매기준율이라 DB증권 적용환율과 다를 수 있습니다.';
const TX=D.accounts||[];
const dtax=TX.reduce((s,a)=>s+a.dividend_tax_usd,0), rtax=TX.reduce((s,a)=>s+a.rp_tax_krw,0);
const dtaxK=dtax*K.fx_now, feeK=D.usd_breakdown.fees*K.fx_now;
document.getElementById('tx').innerHTML='<tr><th>항목</th><th>원화(원)</th><th>USD</th></tr>'+
 `<tr><td>배당 원천세</td><td>${won(dtaxK)}</td><td>${usd2(dtax)}</td></tr>`+
 `<tr><td>RP이자 원천세</td><td>${won(rtax)}</td><td>-</td></tr>`+
 `<tr class="tot"><td>원천징수세 합계</td><td>${won(dtaxK+rtax)}</td><td>-</td></tr>`+
 `<tr><td>수수료·제세금 (참고)</td><td>${won(feeK)}</td><td>${usd(D.usd_breakdown.fees)}</td></tr>`;
document.getElementById('txnote').textContent='원천세는 이미 차감된 금액이라 손익은 세후 기준입니다. 수수료·제세금은 매매손익에 이미 반영되어 있어 별도로 차감하지 않습니다. 배당세는 오늘 환율로 환산했습니다.';
// ---- v7: 법인세 예상 계산기 ----
(function(){
 const CC=D.calc||{}, K0=D.kpi;
 // 세율표 (2026년 이후 가정: 국세청 표와 대조 필요) [과세표준 상한, 세율]
 const TAXB=[[200000000,0.10],[20000000000,0.20],[300000000000,0.22],[Infinity,0.25]], LOCAL=0.10;
 const accs=D.accounts||[];
 const withheld=accs.reduce((s,a)=>s+a.rp_tax_krw+a.dividend_tax_usd*K0.fx_now,0);
 const taxOf=b=>{let t=0,lo=0;for(const [hi,rate] of TAXB){if(b>lo){t+=(Math.min(b,hi)-lo)*rate;}lo=hi;}return t;};
 const now=new Date(D.ts*1000), endY=new Date(now.getFullYear(),11,31,23,59);
 const defM=Math.max(Math.round((endY-now)/86400000/30.4375*10)/10,0);
 const $=id=>document.getElementById(id);
 const num=id=>{const v=parseFloat($(id).value);return isNaN(v)?0:v;};
 let saved={};try{saved=JSON.parse(localStorage.getItem('jyn_tax_in')||'{}');}catch(e){}
 $('t_cost').value=saved.cost||0; $('t_adj').value=saved.adj||0;
 $('t_fx').value=(D.yearend&&D.yearend.rate)?D.yearend.rate:K0.fx_now.toFixed(1);
 $('t_m').value=defM;
 const scen=[['현재 (추가 수익 없음)',0],['월 2%',0.02],['월 3%',0.03],['월 4%',0.04],['월 5%',0.05]];
 if(CC.hist_monthly){scen.push(['과거 실적 평균 (참고)',CC.hist_monthly]);}
 function render(){
  const cost=num('t_cost'),adj=num('t_adj'),X=num('t_fx'),m=num('t_m');
  try{localStorage.setItem('jyn_tax_in',JSON.stringify({cost:cost,adj:adj}));}catch(e){}
  let h='<tr><th>시나리오</th><th>월수익률</th><th>연말 총자산(USD)</th><th>세전이익</th><th>법인세</th><th>지방소득세</th><th>총세액</th><th>기납부(원천세)</th><th>납부예상</th><th>세후이익</th><th>실효세율</th></tr>';
  scen.forEach((sc,i)=>{
   const r=sc[1], ua=K0.total_usd*Math.pow(1+r,m);
   const pre=ua*X-K0.principal_krw-cost;
   const gross=pre+withheld;
   const t1=Math.max(taxOf(gross+adj),0), t2=t1*LOCAL, tt=t1+t2, due=tt-withheld, after=pre-due;
   const eff=gross>0?tt/gross*100:0;
   h+=`<tr${i===0?' class="tot"':''}><td>${sc[0]}</td><td>${(r*100).toFixed(1)}%</td><td>${usd(ua)}</td><td class="${sgn(pre)}">${f1(pre)}</td><td>${f1(t1)}</td><td>${f1(t2)}</td><td>${f1(tt)}</td><td>${f1(withheld)}</td><td>${f1(due)}</td><td class="${sgn(after)}">${f1(after)}</td><td>${eff.toFixed(1)}%</td></tr>`;
  });
  $('taxtbl').innerHTML=h;
 }
 ['t_cost','t_adj','t_fx','t_m'].forEach(id=>$(id).addEventListener('input',render));
 render();
 $('taxnote').innerHTML='금액 단위는 백만원입니다. 가정: 사업연도는 역년(12/31), 세전이익은 연말 환율 평가손익을 포함한 (연말 외화자산×환율 − 투자원금 − 비용), 월수익률은 현재 총자산 기준 복리이며 추가 입출금은 없다고 봅니다. RP이자·배당은 세후 금액으로 잡혀 있어 원천세를 과세표준에 다시 더한 뒤 기납부세액으로 공제했습니다. 이월결손금·세액공제·감면·세무조정은 반영하지 않았고 필요하면 가감 칸에 입력하세요. 세율은 2026년 이후 10/20/22/25%, 지방소득세는 법인세의 10%로 가정했으며 국세청 세율표와 대조가 필요합니다.'+
  (CC.hist_monthly?'<br>참고: 5/4 이후 실적 평균은 월 '+(CC.hist_monthly*100).toFixed(1)+'% (투자손익÷평균 투입원금÷'+CC.hist_months.toFixed(1)+'개월, 환차 제외 단순 평균)입니다.':'')+
  '<br>참고용 추정치이며 실제 신고세액은 세무사 확인이 필요합니다.';
})();
</script></body></html>"""

html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
open(args.out, "w", encoding="utf-8").write(html)
print("생성:", args.out, f"({len(html):,} bytes)")
