import requests, os, re
from datetime import date

# ── Zoho auth ──────────────────────────────────────────────────────────────
def get_access_token():
    r = requests.post("https://accounts.zoho.eu/oauth/v2/token", data={
        "grant_type":    "refresh_token",
        "client_id":     os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
    })
    data = r.json()
    print("Zoho response:", {k:v for k,v in data.items() if k != "access_token"})
    if "access_token" not in data:
        raise Exception(f"Error Zoho: {data}")
    return data["access_token"]

def get_all_deals(token):
    records, page = [], 1
    while True:
        r = requests.get(
            "https://www.zohoapis.eu/crm/v2/Deals",
            headers={"Authorization": f"Zoho-oauthtoken {token}"},
            params={"fields": "Deal_Name,Stage,Account_Name,Responsable_Interno_del_Deal,Calidad_Deal",
                    "per_page": 200, "page": page}
        )
        data = r.json()
        if "data" not in data: break
        records += data["data"]
        if not data.get("info", {}).get("more_records"): break
        page += 1
    return records

# ── Classification ─────────────────────────────────────────────────────────
EXCLUDED_STAGES = {
    "Recámara","Stand by","Descartada/Perdida","Descartada en Ciego","Closed Lost","Closed Lost to Competition",
    "Nunca se presentó","Análisis pero descartado por Albero","Analisis, pero descartado por Albero",
    "No Interesante","Vendido a otro / Potencial comprador","Cerrada y facturada",
    "Cerrada y cobrada","Cerrada y no facturada","-"
}
STATE_MARKERS = {
    "DEAL VIVO","DEAL NO DISPONIBLE","DEAL NO INTERESANTE","Pte. Asignar Comprador",
    "DEAL STAND-BY","DEAL POTENCIAL","DEAL FUTURO","DEAL CERRADO POR ALBERO"
}
RESP_ORDER    = ["CCF","LCT","MGM","AHBV","SLR"]
CALIDAD_ORDER = {"1. Alta":0,"2. Media":1,"3. Baja":2,"4. Pendiente":3}
CALIDAD_COLOR = {"1. Alta":"#4caf50","2. Media":"#fb8c00","3. Baja":"#e53935","4. Pendiente":"#9c27b0"}

def acct(r):
    a = r.get("Account_Name","")
    return a["name"] if isinstance(a, dict) else (a or "")

def clean_resp(r):
    v = r.get("Responsable_Interno_del_Deal") or []
    return v if isinstance(v, list) else [v]

def build_pipeline(records):
    vivos = {}
    for r in records:
        if acct(r) == "DEAL VIVO":
            d = r["Deal_Name"]
            if d and d not in vivos:
                rl = clean_resp(r)
                resp = rl[0] if rl and rl[0] in RESP_ORDER else "CCF"
                vivos[d] = {"resp_matriz": resp, "calidad": r.get("Calidad_Deal")}

    sb, fu = [], []
    seen_sb, seen_fu = set(), set()
    for r in records:
        if r.get("Stage") == "-":
            a = acct(r); d = r["Deal_Name"]
            if a == "DEAL STAND-BY" and d not in seen_sb:
                sb.append(d); seen_sb.add(d)
            elif a in ("DEAL POTENCIAL","DEAL FUTURO") and d not in seen_fu:
                fu.append(d); seen_fu.add(d)

    deal_cands = {d: [] for d in vivos}
    for r in records:
        d = r["Deal_Name"]
        if d not in vivos: continue
        stage = r.get("Stage",""); a = acct(r)
        if stage in EXCLUDED_STAGES or a in STATE_MARKERS: continue
        rl = clean_resp(r)
        resp = rl[0] if rl and rl[0] in RESP_ORDER else vivos[d]["resp_matriz"]
        deal_cands[d].append({"empresa": a, "stage": stage, "resp": resp})

    by_resp = {r: [] for r in RESP_ORDER}
    for d, meta in vivos.items():
        cands = deal_cands[d]
        if cands:
            for c in cands:
                by_resp[c["resp"]].append({"deal":d,"calidad":meta["calidad"],"stage":c["stage"],"empresa":c["empresa"],"warn":False})
        else:
            by_resp[meta["resp_matriz"]].append({"deal":d,"calidad":meta["calidad"],"stage":"Pte. candidatos","empresa":"","warn":True})

    for r in RESP_ORDER:
        normal = [x for x in by_resp[r] if not x["warn"]]
        warns  = [x for x in by_resp[r] if x["warn"]]
        normal.sort(key=lambda x: (CALIDAD_ORDER.get(x["calidad"] or "",4), x["deal"]))
        by_resp[r] = normal + warns

    return by_resp, sb, fu, len(vivos), sum(1 for d in vivos if deal_cands[d])

# ── HTML ────────────────────────────────────────────────────────────────────
def sem(cal, warn=False):
    col = "#999999" if warn else CALIDAD_COLOR.get(cal or "", "#9c27b0")
    return f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{col};margin-right:4px;vertical-align:middle;flex-shrink:0"></span>'

def trunc(s, n):
    s = s or ""
    return (s[:n]+"…") if len(s)>n else s

COLGROUP = """<colgroup>
  <col class="cd"><col class="cf"><col class="ce">
  <col class="cd"><col class="cf"><col class="ce">
  <col class="cd"><col class="cf"><col class="ce">
</colgroup>"""

def resp_tds(by_resp, r, idx):
    items = by_resp[r]
    if idx < len(items):
        item = items[idx]
        bg = "#fffbf0" if item["warn"] else ("#f5f4f0" if idx%2==0 else "#ffffff")
        col = "#b8860b" if item["warn"] else "#62635e"
        st = f'background:{bg};color:{col};'
        return (
            f'<td style="{st}"><div class="dc">{sem(item["calidad"],item["warn"])}<span class="dt2">{trunc(item["deal"],28)}</span></div></td>',
            f'<td style="{st}">{trunc(item["stage"],15)}</td>',
            f'<td style="{st}">{trunc(item["empresa"],18)}</td>'
        )
    return "<td></td>","<td></td>","<td></td>"

def sbfu_tds(sb, fu, idx):
    bg = "#f5f4f0" if idx%2==0 else "#ffffff"
    s = f'<td style="background:{bg};color:#62635e;">{trunc(sb[idx],26)}</td>' if idx<len(sb) else f'<td style="background:{bg}"></td>'
    f = f'<td style="background:{bg};color:#62635e;">{trunc(fu[idx],26)}</td>' if idx<len(fu) else f'<td style="background:{bg}"></td>'
    return s, f, f'<td style="background:{bg}"></td>'

def build_html(by_resp, sb, fu, total, con_cands):
    today    = date.today()
    date_str = today.strftime("%d/%m/%Y")

    max_top = max(len(by_resp[r]) for r in ["CCF","LCT","MGM"])
    max_bot = max(len(by_resp["AHBV"]), len(by_resp["SLR"]), len(sb), len(fu))

    def thead(lbls, subs):
        h = "<thead><tr>"
        for i,l in enumerate(lbls):
            sep = "border-right:2px solid #eebb63;" if i<2 else ""
            h += f'<th colspan="3" class="rh" style="{sep}">{l}</th>'
        h += "</tr><tr>"
        for i,(s1,s2,s3) in enumerate(subs):
            sep = " csep" if i<2 else ""
            h += f'<th class="sh">{s1}</th><th class="sh">{s2}</th><th class="sh{sep}">{s3}</th>'
        return h + "</tr></thead>"

    H = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width">
<title>Pipeline Albero Capital</title>
<style>
@page{{size:A4 landscape;margin:8mm}}
*{{box-sizing:border-box;margin:0;padding:0;font-family:Arial,sans-serif}}
body{{width:277mm;font-size:8px;background:#fff;padding:4px}}
@media print{{
  *{{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}
  col.cd{{width:37mm}}col.cf{{width:23mm}}col.ce{{width:32mm}}
  #login-overlay{{display:none!important}}
}}
#login-overlay{{position:fixed;top:0;left:0;width:100%;height:100%;background:#62635e;display:flex;align-items:center;justify-content:center;z-index:9999}}
#login-box{{background:#fff;padding:32px 40px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.2);min-width:280px}}
#login-box .logo{{font-size:18px;font-weight:bold;color:#eebb63;background:#62635e;padding:6px 16px;letter-spacing:1px;display:inline-block;margin-bottom:20px}}
#login-box input{{width:100%;padding:8px 10px;font-size:13px;border:1px solid #d9d8d2;margin-bottom:12px;outline:none;color:#62635e}}
#login-box button{{width:100%;padding:8px;background:#62635e;color:#eebb63;font-size:13px;font-weight:bold;border:none;cursor:pointer;letter-spacing:1px}}
#login-box button:hover{{background:#4a4b47}}
#login-error{{color:#e53935;font-size:11px;margin-top:6px;display:none}}
#content{{display:none}}
.hdr{{display:flex;justify-content:space-between;align-items:center;border-bottom:1.5px solid #eebb63;margin-bottom:3px;padding-bottom:2px}}
.brand{{font-size:13px;font-weight:bold;color:#eebb63;background:#62635e;padding:3px 9px;letter-spacing:1px}}
.sub{{font-size:8px;color:#62635e;margin-bottom:3px}}
.sep{{height:3px;background:#eebb63;margin:4px 0;opacity:0.35}}
table{{border-collapse:collapse;table-layout:fixed;width:100%}}
.rh{{background:#62635e;color:#eebb63;font-size:8px;font-weight:bold;text-align:center;padding:2px 0}}
.sh{{background:#82827c;color:#f5f4f0;font-size:7px;font-weight:bold;height:11px;line-height:11px;padding:0 2px;white-space:nowrap;overflow:hidden}}
td{{height:11px;line-height:11px;font-size:8px;padding:0 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-bottom:1px solid #d9d8d2;color:#62635e;vertical-align:middle}}
.dc{{display:flex;align-items:center;overflow:hidden}}
.dt2{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.csep{{border-right:2px solid #d9d8d2}}
.ft{{font-size:7px;color:#82827c;text-align:center;margin-top:3px;border-top:1px solid #d9d8d2;padding-top:2px}}
</style>
<script>
function checkPassword(){{
  if(document.getElementById('pwd').value==='Albero109280'){{
    document.getElementById('login-overlay').style.display='none';
    document.getElementById('content').style.display='block';
    sessionStorage.setItem('albero_auth','1');
  }}else{{
    document.getElementById('login-error').style.display='block';
  }}
}}
function checkEnter(e){{if(e.key==='Enter')checkPassword();}}
window.onload=function(){{
  if(sessionStorage.getItem('albero_auth')==='1'){{
    document.getElementById('login-overlay').style.display='none';
    document.getElementById('content').style.display='block';
  }}
}};
</script>
</head><body>
<div id="login-overlay">
  <div id="login-box">
    <div class="logo">ALBERO CAPITAL</div>
    <div style="font-size:12px;color:#82827c;margin-bottom:16px;">Pipeline &mdash; Acceso restringido</div>
    <input type="password" id="pwd" placeholder="Contraseña" onkeypress="checkEnter(event)" autofocus>
    <button onclick="checkPassword()">ENTRAR</button>
    <div id="login-error">Contraseña incorrecta</div>
  </div>
</div>
<div id="content">
<div class="hdr"><div class="brand">ALBERO CAPITAL</div><span style="font-size:8px;color:#82827c">{date_str}</span></div>
<div class="sub">Deals/Pipeline &mdash; Actual Status &nbsp;&middot;&nbsp; Deals vivos: <strong>{total}</strong> &nbsp;|&nbsp; Con candidatos: <strong>{con_cands}</strong></div>
"""

    # TOP: CCF | LCT | MGM
    H += f"<table>{COLGROUP}"
    H += thead(["CCF","LCT","MGM"],[("Deal","Fase","Empresa")]*3)
    H += "<tbody>\n"
    for idx in range(max_top):
        H += "<tr>"
        for ci,r in enumerate(["CCF","LCT","MGM"]):
            t1,t2,t3 = resp_tds(by_resp, r, idx)
            if ci < 2: t3 = re.sub(r"^<td","<td class=\"csep\"",t3)
            H += t1+t2+t3
        H += "</tr>\n"
    H += "</tbody></table>\n"

    H += '<div class="sep"></div>\n'

    # BOTTOM: AHBV | SLR | SB&FU
    H += f"<table>{COLGROUP}"
    H += thead(["AHBV","SLR","STAND-BY &amp; FUTUROS"],
               [("Deal","Fase","Empresa"),("Deal","Fase","Empresa"),("Stand-By","Futuro","")])
    H += "<tbody>\n"
    for idx in range(max_bot):
        H += "<tr>"
        for r in ["AHBV","SLR"]:
            t1,t2,t3 = resp_tds(by_resp, r, idx)
            t3 = re.sub(r"^<td","<td class=\"csep\"",t3)
            H += t1+t2+t3
        s,f,v = sbfu_tds(sb, fu, idx)
        H += s+f+v
        H += "</tr>\n"
    H += "</tbody></table>\n"

    H += f'<div class="ft">Albero Capital &nbsp;&middot;&nbsp; Datos en tiempo real desde Zoho CRM &nbsp;&middot;&nbsp; {date_str}</div>\n'
    H += "</div></body></html>"
    return H

# ── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Obteniendo token...")
    token = get_access_token()
    print("Descargando deals...")
    records = get_all_deals(token)
    print(f"Total registros: {len(records)}")
    by_resp, sb, fu, total, con_cands = build_pipeline(records)
    print(f"Deals vivos: {total} | Con candidatos: {con_cands}")
    html = build_html(by_resp, sb, fu, total, con_cands)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html","w",encoding="utf-8") as f:
        f.write(html)
    print("OK — docs/index.html generado")
